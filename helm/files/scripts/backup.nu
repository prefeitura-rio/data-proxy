use std/log
use ./lib.nu [dump-table execute-sql fail quote-pg rclone-env schema-list sync-config]

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

# Back up the access policy and the access log of one schema, then prune the log.
def backup-schema [schema: string]: nothing -> nothing {
    let dumps = [
        {table: access_policy, file: $'/tmp/($schema)-access_policy.dump'}
        {table: access_log, file: $'/tmp/($schema)-access_log.dump'}
    ]

    let object_date = date now | format date '%Y-%m-%d'
    let object_prefix = $'($env.S3_BUCKET)/($env.BACKUP_PREFIX)/($schema)/($object_date)'
    let remote_prefix = $'store:($object_prefix)'

    log info $'Backup started: schema=($schema)'

    for dump in $dumps {
        backup-table {schema: $schema, remote_prefix: $remote_prefix} $dump
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

# Back up every schema in the configuration, and fail at the end if any schema failed.
def main []: nothing -> nothing {
    load-env {RCLONE_CONFIG: '/dev/null'}
    load-env (rclone-env)

    let failed = sync-config
    | schema-list
    | where {
        let schema = $in

        try {
            backup-schema $schema
            false
        } catch {|err|
            log error $'Backup failed: schema=($schema) error=($err.msg)'
            true
        }
    }

    if ($failed | is-not-empty) {
        fail $'Backup failed for: ($failed | str join ", ")' {
            command: main
            span: (metadata $failed).span
        }
    }
}
