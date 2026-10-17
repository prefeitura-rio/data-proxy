use std/log
use ./lib.nu [
    quote-pg
    execute-sql
    fail
    rbac-context
    wait-for-postgres
]

# Create the roles of the requests: the anonymous role, the roles of rbac.toml, and the PostgREST login
def create-roles []: nothing -> nothing {
    let context = rbac-context ($env.RBAC_FILE? | default ./rbac.toml)

    execute-sql postgres/create_roles.sql {
        roles: ($context.rules.roles | get name | each {|name| quote-pg $name literal } | str join ', ')
        baseline_role: (quote-pg $env.AUTH_USER_ROLE literal)
        anonymous_role: (quote-pg $env.AUTH_ANON_ROLE literal)
        serving_role: (quote-pg $env.AUTH_SERVING_ROLE literal)
        serving_password: (quote-pg $env.SERVING_PASSWORD literal)
    }
    log info 'Request roles setup completed'
}

# Persist the DuckDB secrets. Only a superuser may write them, so this step relies on ops being one.
def configure-ducklake []: nothing -> nothing {
    execute-sql postgres/configure_ducklake.sql {
        s3_key_id: $env.S3_ACCESS_KEY
        s3_secret_key: $env.S3_SECRET_KEY
        s3_endpoint: $env.S3_ENDPOINT
        s3_use_ssl: $env.S3_USE_SSL
        duckdb_password: $env.DUCKDB_PASSWORD
    }
    log info 'DuckLake S3 and catalog reader secret configuration completed'
}

# Let the duckdb role read every metadata schema that the owner role creates in the catalog database
def configure-catalog []: nothing -> nothing {
    wait-for-postgres $env.DUCKLAKE_ADMIN_URL

    execute-sql postgres/configure_catalog.sql --dsn $env.DUCKLAKE_ADMIN_URL {
        writer_role: (quote-pg $env.PGUSER identifier)
        reader_role: (quote-pg duckdb identifier)
    }
    log info 'DuckLake catalog privilege setup completed'
}

# Create the rbac schema and load the rules of the rbac.toml file, all in one transaction
def create-rbac []: nothing -> nothing {
    let context = rbac-context ($env.RBAC_FILE? | default ./rbac.toml)

    execute-sql postgres/create_rbac.sql --single-transaction ($context | merge {
        anonymous_role: (quote-pg $env.AUTH_ANON_ROLE identifier)
    })
    log info $'RBAC rules loaded, file hash ($context.source_hash)'
}

# Stop the job when the roles of the database differ from the roles of the rbac.toml file
def verify-rbac []: nothing -> nothing {
    let context = rbac-context ($env.RBAC_FILE? | default ./rbac.toml)

    execute-sql postgres/verify_rbac.sql {
        roles: ($context.rules.roles | get name | each {|name| quote-pg $name literal } | str join ', ')
        anonymous_role: (quote-pg $env.AUTH_ANON_ROLE literal)
        serving_role: (quote-pg $env.AUTH_SERVING_ROLE literal)
    }
    log info 'Database roles match the rules file'
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
        create-roles
        configure-ducklake
        configure-catalog
        create-rbac
        verify-rbac
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
