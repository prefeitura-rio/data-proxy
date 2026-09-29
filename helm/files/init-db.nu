use std/log
use ./lib.nu [quote-pg execute-sql fail schema-list]

let config = try { open $env.SYNC_CONFIG_PATH } catch {|err| fail $'Failed to open sync config: ($err.msg)' {
    command: init-db
    span: (metadata $env.SYNC_CONFIG_PATH).span
} }

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

def configure-ducklake []: nothing -> nothing {
    execute-sql postgres/configure_ducklake.sql {
        s3_key_id: $env.S3_ACCESS_KEY
        s3_secret_key: $env.S3_SECRET_KEY
        s3_endpoint: $env.S3_ENDPOINT
        s3_use_ssl: $env.S3_USE_SSL
    }
    log info 'Configured persistent DuckLake S3 secret'
}

# Create application schemas with access policy and policy-writer roles
def create-schemas []: nothing -> nothing {
    for schema in (schema-list $config) {
        let scope = (quote-pg $schema literal) + " = ANY(string_to_array(current_setting('app.claim_schemas', true), ','))"

        execute-sql postgres/create_schema.sql {schema: (quote-pg $schema identifier)}

        execute-sql postgres/setup_access_policy.sql {
            schema: (quote-pg $schema identifier)
            user_role: (quote-pg $env.AUTH_USER_ROLE identifier)
            rls_schema: (quote-pg rls identifier)
            scope: $scope
        }

        execute-sql postgres/setup_policy_writer.sql {
            schema: (quote-pg $schema identifier)
            policy_writer_role: (quote-pg $'policy_writer_($schema)' identifier)
            policy_writer_literal: (quote-pg $'policy_writer_($schema)' literal)
            policy_name: (quote-pg $'policy_writer_($schema)' identifier)
            authenticator_role: (quote-pg $env.AUTH_AUTHENTICATOR_ROLE identifier)
        }
    }

    log info 'Created schemas, access policy, and policy-writer roles'
}

# Create the pre_request function that mirrors JWT claims into session variables
def create-pre-request []: nothing -> nothing {
    execute-sql postgres/create_pre_request.sql {}

    execute-sql postgres/grant_rls_usage.sql {
        anonymous_role: (quote-pg $env.AUTH_ANON_ROLE identifier)
        user_role: (quote-pg $env.AUTH_USER_ROLE identifier)
    }

    log info 'Created pre_request function'
}

# Install maintenance procedures used by cleanup and retention jobs.
def install-maintenance []: nothing -> nothing {
    let schema = quote-pg ($env.DBOS_APP_SCHEMA? | default data_proxy) identifier

    for procedure in [cleanup_stale_objects prune_access_log] {
        execute-sql $'postgres/($procedure).sql' {schema: $schema}
    }

    execute-sql postgres/recover_orphaned_workflows.sql {
        schema: $schema
        application_name: (quote-pg ($env.DBOS_APPLICATION_NAME? | default data-proxy-sync) literal)
    }

    log info 'Installed maintenance procedures'
}

def main []: nothing -> nothing {
    log info 'Database initialization started'

    try {
        wait-for-postgres
        configure-ducklake
        create-schemas
        create-pre-request
        install-maintenance

        log info 'Database initialization completed'
    } catch {|err|
        log error $err.msg
        exit 1
    }
}
