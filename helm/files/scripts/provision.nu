use std/log
use ./lib.nu [
    app-schema
    check-result
    data-prefix
    execute-sql
    fail
    legacy-name
    orphans
    query-sql
    quote-identifier
    quote-literal
    rclone-env
    served-schemas
    sync-config
    wait-for-postgres
]

# Import the values file once when the console configuration is empty.
def bootstrap-config []: nothing -> nothing {
    let bootstrap = try { (open --raw $env.SYNC_CONFIG_PATH | decode utf-8) | from json } catch { {
        schemas: {}
    } }

    if ($bootstrap.schemas? | default {} | is-empty) or ((sync-config).schemas | is-not-empty) {
        return
    }

    "BEGIN; SELECT set_config('app.claim_role', 'dp_admin', true); SELECT rbac.load(); SELECT console.import_config(:'document'::jsonb); COMMIT;"
    | psql $env.PG_DATABASE_URL --no-psqlrc --quiet -v ON_ERROR_STOP=1 --set $"document=($bootstrap | to json --raw)"
    | complete
    | check-result 'Import of the values file'
    | ignore

    log info 'The values file filled the empty configuration'
}

# Create catalog reader roles before their corresponding application schemas.
def create-schemas [...names: string]: nothing -> nothing {
    for schema in $names {
        execute-sql postgres/create_schema_reader.sql --dsn $env.DUCKLAKE_ADMIN_URL {
            name: (quote-literal $schema)
            signer: (quote-literal $env.AUTH_SIGNER_ROLE)
            writer: (quote-literal $env.PGUSER)
        }

        execute-sql postgres/create_schema.sql {
            schema: (quote-identifier $schema)
            user_role: (quote-identifier $env.AUTH_USER_ROLE)
        }
    }

    if ($names | is-not-empty) {
        log info $'Provisioned schemas: ($names | str join ",")'
    }
}

# Return configured schemas that the database still lacks.
def unprovisioned-schemas []: nothing -> list<string> {
    let query = "SELECT name FROM console.schemas WHERE to_regnamespace(quote_ident(name)) IS NULL ORDER BY name"
    query-sql $query | lines
}

# Return the current PostgREST schema list from the serving role.
def current-db-schemas []: nothing -> string {
    let query = "SELECT coalesce(max(substring(s FROM '^pgrst\\.db_schemas=(.*)$')), '') FROM pg_db_role_setting AS r INNER JOIN pg_roles AS o ON r.setrole = o.oid CROSS JOIN LATERAL unnest(r.setconfig) AS s WHERE o.rolname = " + (quote-literal $env.AUTH_SERVING_ROLE)
    query-sql $query
}

# Record the schemas that PostgREST serves, or forget the record when the list is empty.
def record-db-schemas [schemas: string]: nothing -> nothing {
    let serving = quote-identifier $env.AUTH_SERVING_ROLE

    if ($schemas | is-empty) {
        execute-sql postgres/reset_db_schemas.sql {serving_role: $serving}
    } else {
        execute-sql postgres/set_db_schemas.sql {
            serving_role: $serving
            db_schemas: (quote-literal $schemas)
        }
    }
}

# Update served schemas and reload PostgREST when the schema list changes.
def publish-schemas [...names: string]: nothing -> nothing {
    let desired = served-schemas ...$names
    let current = current-db-schemas

    if $current == $desired {
        return
    }

    record-db-schemas $desired

    try {
        execute-sql postgres/call_reload_postgrest.sql {
            schema: (app-schema)
        }
    } catch {|err|
        record-db-schemas $current
        fail $'Failed to reload PostgREST: ($err.msg)'
    }

    log info $'PostgREST serves: ($desired)'
}

# Return database schemas that the console no longer configures.
def orphan-schemas [...configured: string]: nothing -> list<string> {
    query-sql "SELECT nspname FROM pg_namespace ORDER BY nspname" | lines | orphans ...$configured
}

# Delete the data files of a schema if they exist.
def purge-data [schema: string]: nothing -> nothing {
    let prefix = data-prefix $env.S3_BUCKET $env.DUCKLAKE_CATALOG_PATH $schema

    let result = with-env ({RCLONE_CONFIG: '/dev/null'} | merge (rclone-env)) {
        rclone purge $prefix | complete
    }

    if $result.exit_code not-in [0 3] {
        fail $'Failed to purge ($prefix): ($result.stderr | str trim)'
    }
}

# Remove obsolete schema files, catalogs and reader roles before their application schemas.
def remove-schemas [...names: string]: nothing -> nothing {
    let app_schema = app-schema

    for schema in $names {
        purge-data $schema

        execute-sql postgres/drop_catalog_schema.sql --dsn $env.DUCKLAKE_ADMIN_URL {
            schema: (quote-identifier $schema)
        }

        execute-sql postgres/drop_schema_reader.sql --dsn $env.DUCKLAKE_ADMIN_URL {
            name: (quote-literal $schema)
        }

        execute-sql postgres/remove_schema.sql {
            schema: (quote-identifier $schema)
            name: (quote-literal $schema)
            app_schema: $app_schema
        }

        log info $'Removed the schema: ($schema)'
    }
}

# Reconcile configured schemas with database schemas, reader roles and PostgREST.
def main []: nothing -> nothing {
    log info 'Schema provisioning started'

    wait-for-postgres $env.PG_DATABASE_URL

    bootstrap-config

    for schema in ((sync-config).schemas | columns) {
        execute-sql postgres/rename_schema_reader.sql --dsn $env.DUCKLAKE_ADMIN_URL {
            old: (quote-literal (legacy-name $schema))
            new: (quote-literal $schema)
        }
    }

    for pass in 0..2 {
        let names = (sync-config).schemas | columns
        let pending = if $env.PROVISION_ALL? == 'true' and $pass == 0 { $names } else { unprovisioned-schemas }
        let removed = orphan-schemas ...$names

        if $pass > 0 and ($pending | is-empty) and ($removed | is-empty) {
            break
        }

        create-schemas ...$pending
        publish-schemas ...$names
        remove-schemas ...$removed
    }

    log info 'Schema provisioning completed'
}
