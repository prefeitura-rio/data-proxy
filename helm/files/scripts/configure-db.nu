use std/log
use ./lib.nu [
    app-schema
    execute-sql
    quote-identifier
    quote-literal
    rbac-context
    role-literals
    wait-for-postgres
    wait-for-relation
]

# Create the roles of the requests: the anonymous role, the roles of rbac.toml, the PostgREST login, and the signer login of the console.
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

# Persist DuckDB secrets with the privileged database role.
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

# Load the rules of the rbac.toml file into the rbac schema that the migrations made, all in one transaction.
def load-rbac []: nothing -> nothing {
    let context = rbac-context

    execute-sql postgres/load_rbac.sql --single-transaction ($context | merge {
        anonymous_role: (quote-identifier $env.AUTH_ANON_ROLE)
    })
    log info $'RBAC rules loaded, file hash ($context.source_hash)'
}

# Stop the job when the roles of the database differ from the roles of the rbac.toml file.
def verify-rbac []: nothing -> nothing {
    let context = rbac-context

    execute-sql postgres/verify_rbac.sql {
        roles: ($context.rules.roles | get name | role-literals)
        anonymous_role: (quote-literal $env.AUTH_ANON_ROLE)
        serving_role: (quote-literal $env.AUTH_SERVING_ROLE)
    }
    log info 'Database roles match the rules file'
}

# Install the chart-configured procedure that requeues workflows from terminated sync pods.
def install-recovery []: nothing -> nothing {
    execute-sql postgres/recover_orphaned_workflows.sql {
        schema: (app-schema)
        application_name: (quote-literal ($env.DBOS_APPLICATION_NAME? | default data-proxy-sync))
    }

    log info 'Workflow recovery procedure setup completed'
}

# Create request roles before the migrations grant their privileges.
def "main roles" []: nothing -> nothing {
    log info 'Request role setup started'

    wait-for-postgres $env.PG_DATABASE_URL
    create-roles
}

# Load the rules and the DuckDB secrets, after the migrations made the rbac schema and the extensions.
def "main rules" []: nothing -> nothing {
    log info 'Rules setup started'

    wait-for-postgres $env.PG_DATABASE_URL
    wait-for-relation rbac.roles
    configure-ducklake
    load-rbac
    verify-rbac
    install-recovery

    log info 'Database initialization completed'
}

# Create request roles, wait for migrations and load the configured rules.
def main []: nothing -> nothing {
    main roles
    main rules
}
