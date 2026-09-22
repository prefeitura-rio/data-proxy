#!/usr/bin/env nu

use std/log
use ./lib.nu [quote-pg render-sql schema-list]

let config = try { open $env.SYNC_CONFIG_PATH } catch {|err| error make {
    msg: $'Failed to open sync config: ($err.msg)'
    label: {
        text: init-db
        span: (metadata $env.SYNC_CONFIG_PATH).span
    }
} }

# Return the list of schemas to process, filtered by SCHEMA env var when set.
# Execute rendered SQL against PostgreSQL
def postgres [query: string]: nothing -> nothing {
    try {
        $query | psql $env.PG_DATABASE_URL --no-psqlrc --quiet -v ON_ERROR_STOP=1
    } catch {|err| error make {
        msg: $'psql failed: ($err.msg)'
        label: {
            text: postgres
            span: (metadata $query).span
        }
    } }
}

# Block until PostgreSQL accepts connections
def wait-for-postgres []: nothing -> nothing {
    loop {
        let result = (
            psql $env.PG_DATABASE_URL --no-psqlrc --quiet -t -A -c 'SELECT 1'
            | complete
        )
        if $result.exit_code == 0 { break }
        log info 'Waiting for PostgreSQL...'
        sleep 2sec
    }
    log info 'PostgreSQL is ready'
}

# Install extensions not already installed by CNPG postInitSQL (idempotent).
def install-extensions []: nothing -> nothing {
    postgres (render-sql install_extensions.sql {})
    log info 'Installed extensions'
}

# Create per-schema freshness tables with RLS policies
def create-schemas-and-freshness []: nothing -> nothing {
    for schema in (schema-list $config) {
        (postgres (render-sql setup_freshness.sql {
            schema: (quote-pg $schema identifier)
            user_role: (quote-pg $env.AUTH_USER_ROLE identifier)
            rls_schema: (quote-pg rls identifier)
            scope: ((quote-pg $schema literal) + " = ANY(string_to_array(current_setting('app.claim_schemas', true), ','))")
        }))
    }

    log info 'Created schemas and freshness tables'
}

# Create the pre_request function that mirrors JWT claims into session variables
def create-pre-request []: nothing -> nothing {
    postgres (render-sql create_pre_request.sql {})

    (postgres (render-sql grant_rls_usage.sql {
        anonymous_role: (quote-pg $env.AUTH_ANON_ROLE identifier)
        user_role: (quote-pg $env.AUTH_USER_ROLE identifier)
    }))

    log info 'Created pre_request function'
}

# Create per-schema access_policy tables with triggers and RLS policies
def create-access-policy []: nothing -> nothing {
    for schema in (schema-list $config) {
        (postgres (render-sql setup_access_policy.sql {
            schema: (quote-pg $schema identifier)
            user_role: (quote-pg $env.AUTH_USER_ROLE identifier)
            scope: ((quote-pg $schema literal) + " = ANY(string_to_array(current_setting('app.claim_schemas', true), ','))")
        }))

        (postgres (render-sql setup_policy_writer.sql {
            schema: (quote-pg $schema identifier)
            policy_writer_role: (quote-pg $'policy_writer_($schema)' identifier)
            policy_writer_literal: (quote-pg $'policy_writer_($schema)' literal)
            policy_name: (quote-pg $'policy_writer_($schema)' identifier)
        }))

        if $env.JOBS_ENABLED == 'true' {
            (
                postgres (render-sql setup_jobs_access.sql {schema: (quote-pg $schema identifier)})
            )
        }
    }

    log info 'Created access policy tables'
}

# Install the PostgreSQL bigquery community extension for BigQuery fallback views
def install-bigquery-extension []: nothing -> nothing {
    postgres (render-sql install_bigquery_extension.sql {})
    log info 'Installed bigquery extension'
}

log info 'Database initialization started'

try {
    wait-for-postgres
    install-extensions
    create-schemas-and-freshness
    create-pre-request
    create-access-policy
    install-bigquery-extension
    postgres (render-sql notify_pgrst.sql {})

    log info 'Database initialization completed'
} catch {|err|
    log error $err.msg
    exit 1
}
