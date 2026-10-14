use std/log
use ./lib.nu [
    execute-sql
    fail
    quote-pg
    schema-list
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

    let result = "SELECT admin.import_config(:'document'::jsonb);"
    | psql $env.PG_DATABASE_URL --no-psqlrc --quiet -v ON_ERROR_STOP=1 --set $"document=($bootstrap | to json --raw)"
    | complete

    if $result.exit_code != 0 {
        fail $'Failed to import the values file: ($result.stderr | str trim)' {
            command: bootstrap-config
            span: (metadata $result).span
        }
    }

    log info 'The values file filled the empty configuration'
}

# Create the schema, its access policy, and its policy-writer role for every configured schema
def create-schemas [...names: string]: nothing -> nothing {
    let app_schema = quote-pg ($env.DBOS_APP_SCHEMA? | default data_proxy) identifier

    for schema in $names {
        let scope = (quote-pg $schema literal) + " = ANY(string_to_array(current_setting('app.claim_schemas', true), ','))"
        let policy_writer_role = quote-pg $'policy_writer_($schema)' identifier

        execute-sql postgres/create_schema.sql {schema: (quote-pg $schema identifier)}

        execute-sql postgres/setup_access_policy.sql {
            schema: (quote-pg $schema identifier)
            user_role: (quote-pg $env.AUTH_USER_ROLE identifier)
            app_schema: $app_schema
            scope: $scope
        }

        execute-sql postgres/setup_policy_writer.sql {
            schema: (quote-pg $schema identifier)
            policy_writer_role: $policy_writer_role
            policy_name: $policy_writer_role
        }

        execute-sql postgres/grant_policy_writer.sql {
            app_schema: $app_schema
            policy_writer_role: $policy_writer_role
            authenticator_role: (quote-pg $env.AUTH_AUTHENTICATOR_ROLE identifier)
        }
    }

    if ($names | is-not-empty) {
        log info $'Provisioned schemas: ($names | str join ",")'
    }
}

# The configured schemas that still lack their schema, access policy, or policy-writer role
def unprovisioned-schemas []: nothing -> list<string> {
    let query = 'SELECT name FROM admin.health WHERE NOT (schema_exists AND access_policy AND policy_writer) ORDER BY name'
    let result = psql $env.PG_DATABASE_URL --no-psqlrc --quiet -t -A -c $query | complete

    if $result.exit_code != 0 {
        fail $'Failed to read the schema health: ($result.stderr | str trim)' {
            command: unprovisioned-schemas
            span: (metadata $result).span
        }
    }

    $result.stdout | lines | where $it != ''
}

# The schemas that PostgREST serves now, as the authenticator role records them
def current-db-schemas []: nothing -> string {
    let query = "SELECT coalesce(max(substring(s FROM '^pgrst\\.db_schemas=(.*)$')), '') FROM pg_db_role_setting AS r INNER JOIN pg_roles AS o ON r.setrole = o.oid CROSS JOIN LATERAL unnest(r.setconfig) AS s WHERE o.rolname = " + (quote-pg $env.AUTH_AUTHENTICATOR_ROLE literal)
    let result = psql $env.PG_DATABASE_URL --no-psqlrc --quiet -t -A -c $query | complete

    if $result.exit_code != 0 {
        fail $'Failed to read the PostgREST schemas: ($result.stderr | str trim)' {
            command: current-db-schemas
            span: (metadata $result).span
        }
    }

    $result.stdout | str trim
}

# Record the schemas that PostgREST serves, or forget the record when the list is empty
def record-db-schemas [schemas: string]: nothing -> nothing {
    let authenticator = quote-pg $env.AUTH_AUTHENTICATOR_ROLE identifier

    if ($schemas | is-empty) {
        execute-sql postgres/reset_db_schemas.sql {authenticator_role: $authenticator}
    } else {
        execute-sql postgres/set_db_schemas.sql {
            authenticator_role: $authenticator
            db_schemas: (quote-pg $schemas literal)
        }
    }
}

# Serve every configured schema and tell PostgREST to reload only when the list changed.
# A failed reload takes the new list back, so that the next run tries again.
def publish-schemas [...names: string]: nothing -> nothing {
    # The first schema is the default profile of PostgREST, so admin comes last.
    let desired = ($names | sort) ++ ['admin'] | str join ','
    let current = current-db-schemas

    if $current == $desired {
        return
    }

    record-db-schemas $desired

    try {
        execute-sql postgres/call_reload_postgrest.sql {
            schema: (quote-pg ($env.DBOS_APP_SCHEMA? | default data_proxy) identifier)
        }
    } catch {|err|
        record-db-schemas $current
        fail $'Failed to reload PostgREST: ($err.msg)' {
            command: publish-schemas
            span: (metadata $desired).span
        }
    }

    log info $'PostgREST serves: ($desired)'
}

def main []: nothing -> nothing {
    log info 'Schema provisioning started'

    wait-for-postgres $env.PG_DATABASE_URL
    bootstrap-config

    let names = sync-config | schema-list

    let pending = if $env.PROVISION_ALL? == 'true' { $names } else { unprovisioned-schemas }

    create-schemas ...$pending

    # The anonymous role reads the one document that the proxy needs, and nothing else of the admin schema.
    execute-sql postgres/grant_proxy_config.sql {
        anonymous_role: (quote-pg $env.AUTH_ANON_ROLE identifier)
    }

    publish-schemas ...$names

    log info 'Schema provisioning completed'
}
