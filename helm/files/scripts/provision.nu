use std/log
use ./lib.nu [
    app-schema
    check-result
    data-prefix
    execute-sql
    fail
    orphans
    query-sql
    quote-identifier
    quote-literal
    rclone-env
    sync-config
    wait-for-postgres
]

# Fill an empty configuration from the values file, once. Editors own it afterward.
def bootstrap-config []: nothing -> nothing {
    let bootstrap = try { open $env.SYNC_CONFIG_PATH } catch { {
        schemas: {}
    } }

    if ($bootstrap.schemas? | default {} | is-empty) or ((sync-config).schemas | is-not-empty) {
        return
    }

    # The job has no token, so it acts as the role that holds import_config, for one transaction.
    "BEGIN; SELECT set_config('app.claim_role', 'dp_admin', true); SELECT rbac.load(); SELECT console.import_config(:'document'::jsonb); COMMIT;"
    | psql $env.PG_DATABASE_URL --no-psqlrc --quiet -v ON_ERROR_STOP=1 --set $"document=($bootstrap | to json --raw)"
    | complete
    | check-result 'Import of the values file'
    | ignore

    log info 'The values file filled the empty configuration'
}

# Create the functions and triggers of the console on the tables and views that the migrations made
def install-console-functions []: nothing -> nothing {
    for template in [
        postgres/create_console_functions.sql
        postgres/create_console_config.sql
        postgres/create_console_triggers.sql
    ] {
        execute-sql $template
    }

    log info 'Console functions setup completed'
}

# Create the schema, its access policy and the role that reads its metadata, for every given schema
def create-schemas [...names: string]: nothing -> nothing {
    let app_schema = app-schema

    for schema in $names {
        let scope = 'rbac.mine(' + (quote-literal $schema) + ')'

        execute-sql postgres/create_schema.sql {schema: (quote-identifier $schema)}

        execute-sql postgres/setup_access_policy.sql {
            schema: (quote-identifier $schema)
            user_role: (quote-identifier $env.AUTH_USER_ROLE)
            app_schema: $app_schema
            scope: $scope
        }

        execute-sql postgres/create_schema_reader.sql --dsn $env.DUCKLAKE_ADMIN_URL {
            name: (quote-literal $schema)
            signer: (quote-literal $env.AUTH_SIGNER_ROLE)
            writer: (quote-literal $env.PGUSER)
        }
    }

    if ($names | is-not-empty) {
        log info $'Provisioned schemas: ($names | str join ",")'
    }
}

# The configured schemas that still lack their schema or their access policy
def unprovisioned-schemas []: nothing -> list<string> {
    let query = "SELECT name FROM console.schemas WHERE to_regclass(format('%I.access_policy', name)) IS NULL ORDER BY name"
    query-sql $query | lines
}

# The schemas that PostgREST serves now, as the serving login records them
def current-db-schemas []: nothing -> string {
    let query = "SELECT coalesce(max(substring(s FROM '^pgrst\\.db_schemas=(.*)$')), '') FROM pg_db_role_setting AS r INNER JOIN pg_roles AS o ON r.setrole = o.oid CROSS JOIN LATERAL unnest(r.setconfig) AS s WHERE o.rolname = " + (quote-literal $env.AUTH_SERVING_ROLE)
    query-sql $query
}

# Record the schemas that PostgREST serves, or forget the record when the list is empty
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

# Serve every configured schema and tell PostgREST to reload only when the list changed.
# A failed reload takes the new list back, so that the next run tries again.
def publish-schemas [...names: string]: nothing -> nothing {
    # The first schema is the default profile of PostgREST, so console comes last.
    let desired = ($names | sort) ++ ['console'] | str join ','
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

# The schemas of the database that the console no longer lists, which a removal pass drops.
def orphan-schemas [...configured: string]: nothing -> list<string> {
    let query = "SELECT schemaname, tablename FROM pg_tables WHERE tablename IN ('access_policy', 'access_log')"
    query-sql $query | lines | split column ',' schema table | orphans ...$configured
}

# Delete the data files of one schema. A folder that does not exist is already gone.
def purge-data [schema: string]: nothing -> nothing {
    let prefix = data-prefix $env.S3_BUCKET $env.DUCKLAKE_CATALOG_PATH $schema

    let result = with-env ({RCLONE_CONFIG: '/dev/null'} | merge (rclone-env)) {
        rclone purge $prefix | complete
    }

    if $result.exit_code not-in [0 3] {
        fail $'Failed to purge ($prefix): ($result.stderr | str trim)'
    }
}

# Remove every schema that the console dropped: the files, the catalog, its reader role, and then the schema of the database.
# The schema of the database goes last, because the next run finds an unfinished removal through it.
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

# Make the database match the console configuration: create, serve and remove schemas.
def main []: nothing -> nothing {
    log info 'Schema provisioning started'

    wait-for-postgres $env.PG_DATABASE_URL

    # The event run skips this step, because replacing a trigger locks its table.
    if $env.PROVISION_ALL? == 'true' {
        install-console-functions
    }

    bootstrap-config

    # Every token switches to one role. It needs the rights on the console schema once the migrations created them.
    execute-sql postgres/grant_console_usage.sql {
        user_role: (quote-identifier $env.AUTH_USER_ROLE)
    }

    # The anonymous role reads the one document that the proxy needs, and nothing else of the console schema.
    execute-sql postgres/grant_proxy_config.sql {
        anonymous_role: (quote-identifier $env.AUTH_ANON_ROLE)
    }

    # Each pass reads both lists again and the loop repeats up to three times, so that an edit made during the run is not lost.
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
