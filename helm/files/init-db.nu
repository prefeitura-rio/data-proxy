#!/usr/bin/env nu

use std/log
use ./lib.nu [quote-pg render-sql]

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

# Remove the obsolete filesystem-backed DuckDB S3 secret from every CNPG instance.
def remove-legacy-s3-secret []: nothing -> nothing {
    let selector = $'cnpg.io/cluster=($env.CNPG_CLUSTER_NAME)'
    let pods = (
        kubectl get pods -n $env.KUBERNETES_NAMESPACE -l $selector
            -o 'jsonpath={range .items[*]}{.metadata.name}{"\\n"}{end}'
        | lines
        | where ($it | is-not-empty)
    )

    if ($pods | is-empty) {
        error make {msg: $'No Pods found for CNPG Cluster ($env.CNPG_CLUSTER_NAME)'}
    }

    for pod in $pods {
        let result = (
            kubectl exec -n $env.KUBERNETES_NAMESPACE $pod -c postgres --
                rm -f /var/lib/postgresql/data/.duckdb/stored_secrets/s3.duckdb_secret
            | complete
        )
        if $result.exit_code != 0 {
            error make {msg: $'Failed to remove legacy S3 secret from ($pod): ($result.stderr)'}
        }
    }

    log info $'Removed legacy filesystem S3 secrets from ($pods | length) CNPG Pods'
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
            schema: (quote-pg $schema identifier)
            user_role: (quote-pg $env.AUTH_USER_ROLE identifier)
            rls_schema: (quote-pg rls identifier)
            scope: ((quote-pg $schema literal) + " = ANY(string_to_array(current_setting('app.claim_schemas', true), ','))")
        }))

        if $env.BACKUP_ENABLED == 'true' {
            (
                postgres (render-sql grant_schema_usage_backup.sql {schema: (quote-pg $schema identifier)})
            )
        }
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
    for schema in (schema-list) {
        (postgres (render-sql setup_access_policy.sql {
            schema: (quote-pg $schema identifier)
            user_role: (quote-pg $env.AUTH_USER_ROLE identifier)
            scope: ((quote-pg $schema literal) + " = ANY(string_to_array(current_setting('app.claim_schemas', true), ','))")
        }))

        (postgres (render-sql setup_policy_writer.sql {
            schema: (quote-pg $schema identifier)
            policy_writer_role: (quote-pg $'policy_writer_($schema)' identifier)
            policy_writer_literal: (quote-pg $'policy_writer_($schema)' literal)
            authenticator_role: (quote-pg $env.AUTH_AUTHENTICATOR_ROLE identifier)
            policy_name: (quote-pg $'policy_writer_($schema)' identifier)
        }))

        if $env.BACKUP_ENABLED == 'true' {
            (
                postgres (render-sql setup_access_policy_backup.sql {schema: (quote-pg $schema identifier)})
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
    remove-legacy-s3-secret
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
