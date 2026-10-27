use std/log
use ./lib.nu [
    app-schema
    execute-sql
    quote-identifier
    quote-literal
    rbac-context
    role-literals
    wait-for-postgres
]

# Create the roles of the requests: the anonymous role, the roles of rbac.toml, the PostgREST login, and the signer login of the console
def create-roles []: nothing -> nothing {
    let context = rbac-context

    execute-sql postgres/create_roles.sql {
        roles: ($context.rules.roles | get name | role-literals)
        baseline_role: (quote-literal $env.AUTH_USER_ROLE)
        anonymous_role: (quote-literal $env.AUTH_ANON_ROLE)
        serving_role: (quote-literal $env.AUTH_SERVING_ROLE)
        serving_password: (quote-literal $env.SERVING_PASSWORD)
        signer_role: (quote-literal $env.AUTH_SIGNER_ROLE)
        signer_password: (quote-literal $env.SIGNER_PASSWORD)
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
        catalog_port: ($env.CATALOG_PORT? | default 5432)
    }
    log info 'DuckLake S3 and catalog reader secret configuration completed'
}

# Let the duckdb role read every metadata schema that the owner role creates in the catalog database
def configure-catalog []: nothing -> nothing {
    wait-for-postgres $env.DUCKLAKE_ADMIN_URL

    execute-sql postgres/configure_catalog.sql --dsn $env.DUCKLAKE_ADMIN_URL {
        writer_role: (quote-identifier $env.PGUSER)
        reader_role: (quote-identifier duckdb)
    }
    log info 'DuckLake catalog privilege setup completed'
}

# Create the rbac schema and load the rules of the rbac.toml file, all in one transaction
def create-rbac []: nothing -> nothing {
    let context = rbac-context

    execute-sql postgres/create_rbac.sql --single-transaction ($context | merge {
        anonymous_role: (quote-identifier $env.AUTH_ANON_ROLE)
    })
    log info $'RBAC rules loaded, file hash ($context.source_hash)'
}

# Stop the job when the roles of the database differ from the roles of the rbac.toml file
def verify-rbac []: nothing -> nothing {
    let context = rbac-context

    execute-sql postgres/verify_rbac.sql {
        roles: ($context.rules.roles | get name | role-literals)
        anonymous_role: (quote-literal $env.AUTH_ANON_ROLE)
        serving_role: (quote-literal $env.AUTH_SERVING_ROLE)
    }
    log info 'Database roles match the rules file'
}

# Create the table where the console keeps the sealed keys of its storage identities, in the catalog database
def create-signer-keys []: nothing -> nothing {
    wait-for-postgres $env.DUCKLAKE_ADMIN_URL

    execute-sql postgres/create_signer_keys.sql --dsn $env.DUCKLAKE_ADMIN_URL {
        signer: (quote-literal $env.AUTH_SIGNER_ROLE)
    }
    log info 'Signer key table setup completed'
}

# Create the schema and the tables where the engine keeps its state and its errors
def create-state []: nothing -> nothing {
    execute-sql postgres/create_state.sql {
        schema: (app-schema)
    }
    log info 'Application state tables setup completed'
}

# Create the functions that tell PostgREST and the proxy to read the database again
def create-reload []: nothing -> nothing {
    execute-sql postgres/create_reload.sql {
        schema: (app-schema)
    }
    log info 'Reload functions setup completed'
}

# Create the pre_request function that mirrors JWT claims into session variables
def create-pre-request []: nothing -> nothing {
    let schema = app-schema
    execute-sql postgres/create_pre_request.sql {schema: $schema}

    execute-sql postgres/grant_app_usage.sql {
        app_schema: $schema
        anonymous_role: (quote-identifier $env.AUTH_ANON_ROLE)
        user_role: (quote-identifier $env.AUTH_USER_ROLE)
    }

    log info 'PostgREST pre-request function setup completed'
}

# Install the maintenance procedures so the maintenance job can run before any sync.
def install-maintenance []: nothing -> nothing {
    let schema = app-schema

    execute-sql postgres/cleanup_stale_objects.sql {
        schema: $schema
    }

    execute-sql postgres/prune_access_log.sql {
        schema: $schema
    }

    execute-sql postgres/prune_workflows.sql {
        schema: $schema
    }

    execute-sql postgres/recover_orphaned_workflows.sql {
        schema: $schema
        application_name: (quote-literal ($env.DBOS_APPLICATION_NAME? | default data-proxy-sync))
    }

    log info 'Database maintenance procedure setup completed'
}

# Create the roles, the schemas and the functions that the database needs before the first sync.
def main []: nothing -> nothing {
    log info 'Database initialization started'

    wait-for-postgres $env.PG_DATABASE_URL
    create-roles
    configure-ducklake
    configure-catalog
    create-rbac
    verify-rbac
    create-signer-keys
    create-state
    create-reload
    create-pre-request
    install-maintenance

    log info 'Database initialization completed'
}
