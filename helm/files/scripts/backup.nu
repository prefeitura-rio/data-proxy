use std/log
use ./lib.nu [
    app-schema
    backup-plan
    dump-table
    execute-sql
    fail
    rclone-env
    schema-list
    sync-config
]

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
        fail $'S3 upload failed for ($context.schema).($dump.table): ($err.msg)'
    }
}

# Back up the access policy and the access log of one schema, then prune the log.
def backup-schema [schema: string]: nothing -> nothing {
    let object_date = date now | format date '%Y-%m-%d'
    let plan = backup-plan $schema $env.S3_BUCKET $env.BACKUP_PREFIX $object_date
    let dumps = $plan.dumps
    let remote_prefix = $plan.remote_prefix

    log info $'Backup started: schema=($schema)'

    for dump in $dumps {
        backup-table {schema: $schema, remote_prefix: $remote_prefix} $dump
    }

    log info $'Access log retention pruning started: schema=($schema)'
    execute-sql postgres/call_prune_access_log.sql {schema: (app-schema)} --vars {
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
    | reduce --fold [] {|schema, failed|
        try {
            backup-schema $schema

            $failed
        } catch {|err|
            log error $'Backup failed: schema=($schema) error=($err.msg)'

            $failed | append $schema
        }
    }

    if ($failed | is-not-empty) {
        fail $'Backup failed for: ($failed | str join ", ")'
    }
}
