use std/log
use ./lib.nu [dump-table quote-pg execute-sql fail]

# Return the configured object-store endpoint with an explicit scheme.
def endpoint-url []: nothing -> string {
    let endpoint = $env.S3_ENDPOINT

    if ($endpoint | str starts-with 'http') {
        return $endpoint
    }

    let scheme = if ($env.S3_USE_SSL? | default 'false') == 'true' { 'https' } else { 'http' }

    $'($scheme)://($endpoint)'
}

# Return the rclone remote environment for the configured backup target.
def rclone-env []: nothing -> record {
    let endpoint = endpoint-url

    if ($endpoint | str contains 'googleapis.com') {
        return {
            RCLONE_CONFIG_STORE_TYPE: 'google cloud storage'
            RCLONE_CONFIG_STORE_SERVICE_ACCOUNT_FILE: $env.GOOGLE_APPLICATION_CREDENTIALS
            RCLONE_CONFIG_STORE_BUCKET_POLICY_ONLY: 'true'
        }
    }

    {
        RCLONE_CONFIG_STORE_TYPE: 's3'
        RCLONE_CONFIG_STORE_PROVIDER: 'Other'
        RCLONE_CONFIG_STORE_ENDPOINT: $endpoint
        RCLONE_CONFIG_STORE_ACCESS_KEY_ID: $env.AWS_ACCESS_KEY_ID
        RCLONE_CONFIG_STORE_SECRET_ACCESS_KEY: $env.AWS_SECRET_ACCESS_KEY
        RCLONE_CONFIG_STORE_FORCE_PATH_STYLE: 'true'
        RCLONE_CONFIG_STORE_REGION: 'auto'
        RCLONE_CONFIG_STORE_NO_CHECK_BUCKET: 'true'
    }
}

# Dump one PostgreSQL table and upload it to the configured object store.
def backup-table [context: record<schema: string, remote_prefix: string>, dump: record<table: string, file: string>]: nothing -> nothing {
    log info $'Backup table dump started: schema=($context.schema) table=($dump.table)'
    dump-table $env.PG_DATABASE_URL {
        schema: $context.schema
        table: $dump.table
        file: $dump.file
    }

    log info $'Backup file upload started: file=($dump.file)'
    try {
        rclone copyto $dump.file $'($context.remote_prefix)/($dump.table).dump'
    } catch {|err|
        fail $'S3 upload failed for ($context.schema).($dump.table): ($err.msg)' {
            command: rclone
            span: (metadata $dump.table).span
        }
    }
}

def main []: nothing -> nothing {
    let schema = $env.SCHEMA? | default test
    let dumps = [
        {table: access_policy, file: '/tmp/access_policy.dump'}
        {table: access_log, file: '/tmp/access_log.dump'}
    ]

    let object_date = date now | format date '%Y-%m-%d'
    let object_prefix = $'($env.S3_BUCKET)/($env.BACKUP_PREFIX)/($schema)/($object_date)'
    let remote_prefix = $'store:($object_prefix)'

    log info $'Backup started: schema=($schema)'

    load-env {RCLONE_CONFIG: '/dev/null'}
    load-env (rclone-env)

    for dump in $dumps {
        backup-table {schema: $schema, remote_prefix: $remote_prefix} $dump
    }

    log info $'DuckLake catalog backup started: schema=($schema)'
    let catalog_path = $'($env.DUCKLAKE_CATALOG_PATH)/($schema)/catalog.sqlite'
    if ($catalog_path | path exists) {
        try {
            rclone copyto $catalog_path $'($remote_prefix)/catalog.sqlite'
        } catch {|err|
            fail $'Catalog upload failed for ($schema): ($err.msg)' {
                command: rclone
                span: (metadata $schema).span
            }
        }
    } else {
        log warning $'DuckLake catalog backup skipped: catalog=($catalog_path) reason=not-found'
    }

    log info $'Access log retention pruning started: schema=($schema)'
    let procedure_schema = quote-pg ($env.DBOS_APP_SCHEMA? | default data_proxy) identifier

    execute-sql postgres/call_prune_access_log.sql {
        schema: $procedure_schema
    } --vars {
        retention: $"($env.ACCESS_LOG_RETENTION_DAYS) days"
        schema: $schema
    }

    log info $'Backup completed: schema=($schema)'

    try {
        for file in ($dumps | get file) {
            rm --force $file
        }
    } catch {|err| log warning $'Backup dump cleanup skipped: error=($err.msg)' }
}
