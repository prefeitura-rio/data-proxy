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
    log info $'Dumping ($context.schema).($dump.table)...'
    dump-table $env.PG_DATABASE_URL {
        schema: $context.schema
        table: $dump.table
        file: $dump.file
    }

    log info $'Uploading ($dump.file)...'
    try {
        rclone copyto $dump.file $'($context.remote_prefix)/($dump.table).dump'
    } catch {|err| fail $'S3 upload failed for ($context.schema).($dump.table): ($err.msg)' {
            command: rclone
            span: (metadata $dump.table).span
        } }
}

    execute-sql postgres/call_prune_access_log.sql {
        schema: $procedure_schema
    } --vars {
        retention: $"($env.ACCESS_LOG_RETENTION_DAYS) days"
        schema: $schema
    }

    log info $'Backup completed schema=($schema)'

    try {
        for file in ($dumps | get file) {
            rm --force $file
        }
    } catch {|err| log warning $'Could not remove dumps: ($err.msg)' }
}
