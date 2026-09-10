#!/usr/bin/env nu

use std/log

let config = try { open $env.SYNC_CONFIG_PATH } catch {|err| error make {
    msg: $'Failed to open sync config: ($err.msg)'
    label: {
        text: init-db
        span: (metadata $env.SYNC_CONFIG_PATH).span
    }
} }

let sql_dir = '/sql'

# Load a SQL template file from the mounted sql directory
def load-sql [name: path]: nothing -> string {
    try {
        open ($sql_dir | path join $name)
    } catch {|err| error make {
        msg: $'Failed to load SQL template ($name): ($err.msg)'
        label: {
            text: load-sql
            span: (metadata $name).span
        }
    } }
}

# Execute a SQL statement against PostgreSQL with optional psql variables
def postgres [query: string, ...vars: string]: nothing -> nothing {
    let args = $vars | each {|v| [-v $v] } | flatten

    try {
        $query | psql $env.PG_DSN --no-psqlrc --quiet -v ON_ERROR_STOP=1 ...$args
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

# Install PostGIS, pg_duckdb, and the sync_status enum type
def install-extensions []: nothing -> nothing {
    postgres (load-sql install_extensions.sql)
    log info 'Installed extensions'
}

# Create anon, user, authenticator, and backup roles with grants
def create-roles []: nothing -> nothing {
    (postgres
        (load-sql create_roles.sql)
        $'anon_role=($env.AUTH_ANON_ROLE)'
        $'user_role=($env.AUTH_USER_ROLE)'
        $'authenticator_role=($env.AUTH_AUTHENTICATOR_ROLE)'
    )
    (postgres
        (load-sql set_role_nologin.sql)
        $'role=($env.AUTH_ANON_ROLE)'
    )
    (postgres
        (load-sql set_role_nologin.sql)
        $'role=($env.AUTH_USER_ROLE)'
    )
    (postgres
        (load-sql set_authenticator_password.sql)
        $'authenticator_role=($env.AUTH_AUTHENTICATOR_ROLE)'
        $'auth_password=($env.PGRST_AUTHENTICATOR_PASSWORD)'
    )
    (postgres
        (load-sql grant_role_to_authenticator.sql)
        $'role=($env.AUTH_ANON_ROLE)'
        $'authenticator_role=($env.AUTH_AUTHENTICATOR_ROLE)'
    )
    (postgres
        (load-sql grant_role_to_authenticator.sql)
        $'role=($env.AUTH_USER_ROLE)'
        $'authenticator_role=($env.AUTH_AUTHENTICATOR_ROLE)'
    )

    if $env.BACKUP_ENABLED == 'true' {
        (postgres
            (load-sql create_backup_role.sql)
            $'backup_password=($env.BACKUP_PASSWORD)'
        )
        log info 'Created backup role'
    }

    log info 'Created roles'
}

# Create per-schema freshness tables with RLS policies
def create-schemas-and-freshness []: nothing -> nothing {
    for schema in ($config.schemas | columns) {
        let schema_var = $'schema=($schema)'
        let user_var = $'user_role=($env.AUTH_USER_ROLE)'

        (postgres (load-sql setup_freshness.sql) $schema_var $user_var)

        if $env.BACKUP_ENABLED == 'true' {
            (postgres (load-sql grant_schema_usage_backup.sql) $schema_var)
        }
    }

    log info 'Created schemas and freshness tables'
}

# Create the pre_request function that mirrors JWT claims into session variables
def create-pre-request []: nothing -> nothing {
    postgres (load-sql create_pre_request.sql)
    log info 'Created pre_request function'
}

# Create per-schema access_policy tables with triggers and RLS policies
def create-access-policy []: nothing -> nothing {
    for schema in ($config.schemas | columns) {
        let schema_var = $'schema=($schema)'
        let user_var = $'user_role=($env.AUTH_USER_ROLE)'

        (postgres (load-sql setup_access_policy.sql) $schema_var $user_var)

        if $env.BACKUP_ENABLED == 'true' {
            (postgres (load-sql setup_access_policy_backup.sql) $schema_var)
        }
    }

    log info 'Created access policy tables'
}

# Create the DuckDB S3 secret for pgduckdb read_parquet access to GCS
def create-s3-secret []: nothing -> nothing {
    (postgres
        (load-sql create_s3_secret.sql)
        $'gcs_key_id=($env.GCS_KEY_ID)'
        $'gcs_secret_key=($env.GCS_SECRET_KEY)'
        $'gcs_endpoint=($env.GCS_ENDPOINT)'
        $'gcs_use_ssl=($env.GCS_USE_SSL)'
    )
    log info 'Created DuckDB S3 secret'
}

# Install the DuckDB bigquery community extension for BigQuery fallback views
def install-bigquery-extension []: nothing -> nothing {
    if $env.FALLBACK_ENABLED == 'true' {
        postgres (load-sql install_bigquery_extension.sql)
        log info 'Installed bigquery extension'
    }
}

# Set pg_duckdb GUCs. Every later DuckDB query needs them, and the community
# bigquery extension needs the unsigned and community extensions enabled.
def set-pg-duckdb-gucs []: nothing -> nothing {
    if $env.FALLBACK_ENABLED == 'true' {
        postgres (load-sql pg_duckdb_gucs.sql)
        log info 'Set pg_duckdb GUCs'
    }
}

log info 'Database initialization started'

try {
    wait-for-postgres
    install-extensions
    set-pg-duckdb-gucs
    create-roles
    create-schemas-and-freshness
    create-pre-request
    create-access-policy
    create-s3-secret
    install-bigquery-extension
    postgres (load-sql notify_pgrst.sql)

    log info 'Database initialization completed'
} catch {|err|
    log error $err.msg
    exit 1
}
