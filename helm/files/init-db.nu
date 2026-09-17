#!/usr/bin/env nu

use std/log
use lib.nu render-sql

let config = try { open $env.SYNC_CONFIG_PATH } catch {|err| error make {
    msg: $'Failed to open sync config: ($err.msg)'
    label: {
        text: init-db
        span: (metadata $env.SYNC_CONFIG_PATH).span
    }
} }

# Return the list of schemas to process, filtered by SCHEMA env var when set.
def schema-list []: nothing -> list<string> {
    let all = $config.schemas | columns
    let target = $env.SCHEMA?
    if $target == null or ($target | is-empty) { $all } else {
        $all | where $it == $target
    }
}

# Execute rendered SQL against PostgreSQL
def postgres [query: string]: nothing -> nothing {
    try {
        $query | psql $env.PG_DSN --no-psqlrc --quiet -v ON_ERROR_STOP=1
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
            psql $env.PG_DSN --no-psqlrc --quiet -t -A -c 'SELECT 1'
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
    for schema in (schema-list) {
        (postgres (render-sql setup_freshness.sql {
            schema: $schema
            user_role: $env.AUTH_USER_ROLE
            rls_schema: rls
            scope: ($schema + " = ANY(string_to_array(current_setting('app.claim_schemas', true), ','))")
        }))

        if $env.BACKUP_ENABLED == 'true' {
            (
                postgres (render-sql grant_schema_usage_backup.sql {schema: $schema})
            )
        }
    }

    log info 'Created schemas and freshness tables'
}

# Create the pre_request function that mirrors JWT claims into session variables
def create-pre-request []: nothing -> nothing {
    postgres (render-sql create_pre_request.sql {})

    (postgres (render-sql grant_rls_usage.sql {
        anonymous_role: $env.AUTH_ANON_ROLE
        user_role: $env.AUTH_USER_ROLE
    }))

    log info 'Created pre_request function'
}

# Create per-schema access_policy tables with triggers and RLS policies
def create-access-policy []: nothing -> nothing {
    for schema in (schema-list) {
        (postgres (render-sql setup_access_policy.sql {
            schema: $schema
            user_role: $env.AUTH_USER_ROLE
            scope: ($schema + " = ANY(string_to_array(current_setting('app.claim_schemas', true), ','))")
        }))

        (postgres (render-sql setup_policy_writer.sql {
            schema: $schema
            policy_writer_role: $'policy_writer_($schema)'
            authenticator_role: $env.AUTH_AUTHENTICATOR_ROLE
            policy_name: $'policy_writer_($schema)'
        }))

        if $env.BACKUP_ENABLED == 'true' {
            (
                postgres (render-sql setup_access_policy_backup.sql {schema: $schema})
            )
        }
    }

    log info 'Created access policy tables'
}

# Create the PostgreSQL S3 secret for postgres read_parquet access to the bucket
def create-s3-secret []: nothing -> nothing {
    (postgres (render-sql create_s3_secret.sql {
        s3_key_id: $env.S3_ACCESS_KEY
        s3_secret_key: $env.S3_SECRET_KEY
        s3_endpoint: $env.S3_ENDPOINT
        s3_use_ssl: $env.S3_USE_SSL
    }))
    log info 'Created PostgreSQL S3 secret'
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
    create-s3-secret
    install-bigquery-extension
    postgres (render-sql notify_pgrst.sql {})

    log info 'Database initialization completed'
} catch {|err|
    log error $err.msg
    exit 1
}
