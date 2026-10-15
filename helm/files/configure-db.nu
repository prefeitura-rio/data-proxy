use std/log
use ./lib.nu [
    quote-pg
    execute-sql
    fail
    wait-for-postgres
]

def configure-ducklake []: nothing -> nothing {
    execute-sql postgres/configure_ducklake.sql {
        s3_key_id: $env.S3_ACCESS_KEY
        s3_secret_key: $env.S3_SECRET_KEY
        s3_endpoint: $env.S3_ENDPOINT
        s3_use_ssl: $env.S3_USE_SSL
        reader_password: $env.DUCKLAKE_READER_PASSWORD
    }
    log info 'DuckLake S3 and catalog reader secret configuration completed'
}

# Let the reader role read every metadata schema that the writer role creates in the catalog database
def configure-catalog []: nothing -> nothing {
    wait-for-postgres $env.DUCKLAKE_ADMIN_URL

    execute-sql postgres/configure_catalog.sql --dsn $env.DUCKLAKE_ADMIN_URL {
        writer_role: (quote-pg ducklake_writer identifier)
        reader_role: (quote-pg ducklake_reader identifier)
    }
    log info 'DuckLake catalog privilege setup completed'
}

# Create the schema and the tables where the engine keeps its state and its errors
def create-state []: nothing -> nothing {
    execute-sql postgres/create_state.sql {
        schema: (quote-pg ($env.DBOS_APP_SCHEMA? | default data_proxy) identifier)
    }
    log info 'Application state tables setup completed'
}

# Create the functions that tell PostgREST and the proxy to read the database again
def create-reload []: nothing -> nothing {
    execute-sql postgres/create_reload.sql {
        schema: (quote-pg ($env.DBOS_APP_SCHEMA? | default data_proxy) identifier)
    }
    log info 'Reload functions setup completed'
}

# Create the pre_request function that mirrors JWT claims into session variables
def create-pre-request []: nothing -> nothing {
    let schema = quote-pg ($env.DBOS_APP_SCHEMA? | default data_proxy) identifier
    execute-sql postgres/create_pre_request.sql {schema: $schema}

    execute-sql postgres/grant_app_usage.sql {
        app_schema: $schema,
        anonymous_role: (quote-pg $env.AUTH_ANON_ROLE identifier),
        user_role: (quote-pg $env.AUTH_USER_ROLE identifier)
    }

    # PostgREST applies the role settings of the request role only, so each level gets its own call.
    for role in [console_viewer console_operator console_editor console_admin] {
        execute-sql postgres/grant_console_usage.sql {
            app_schema: $schema
            console_role: $role
        }
    }

    log info 'PostgREST pre-request function setup completed'
}

# Install the maintenance procedures so the maintenance job can run before any sync.
def install-maintenance []: nothing -> nothing {
    let schema = quote-pg ($env.DBOS_APP_SCHEMA? | default data_proxy) identifier

    execute-sql postgres/cleanup_stale_objects.sql {
        schema: $schema
    }

    execute-sql postgres/prune_access_log.sql {
        schema: $schema
    }

    execute-sql postgres/recover_orphaned_workflows.sql {
        schema: $schema
        application_name: (quote-pg ($env.DBOS_APPLICATION_NAME? | default data-proxy-sync) literal)
    }

    log info 'Database maintenance procedure setup completed'
}

def main []: nothing -> nothing {
    log info 'Database initialization started'

    try {
        wait-for-postgres $env.PG_DATABASE_URL
        configure-ducklake
        configure-catalog
        create-state
        create-reload
        create-pre-request
        install-maintenance

        log info 'Database initialization completed'
    } catch {|err| fail $err.msg {
            command: main
            span: (metadata $env.PG_DATABASE_URL).span
        } }
}
