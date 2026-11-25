use std/log
use ./lib.nu [
    backup-plan
    dump-table
    execute-sql
    fail
    rclone-env
]

# Dump one table of the schema access and upload it to the configured object store.
def backup-table [remote_prefix: string, dump: record<table: string, file: string>]: nothing -> nothing {
    log info $'Backup table dump started: table=($dump.table)'
    dump-table $env.PG_DATABASE_URL {
        schema: access
        table: $dump.table
        file: $dump.file
    }

    log info $'Backup file upload started: file=($dump.file)'
    try {
        rclone copyto $dump.file $'($remote_prefix)/($dump.table).dump'
    } catch {|err|
        fail $'S3 upload failed for access.($dump.table): ($err.msg)'
    }
}

# Back up the access policy and the access log, then prune the log.
def main []: nothing -> nothing {
    load-env {RCLONE_CONFIG: '/dev/null'}
    load-env (rclone-env)

    let object_date = date now | format date '%Y-%m-%d'
    let plan = backup-plan $env.S3_BUCKET $env.BACKUP_PREFIX $object_date

    log info 'Backup started'

    for dump in $plan.dumps {
        backup-table $plan.remote_prefix $dump
    }

    log info 'Access log retention pruning started'
    execute-sql postgres/call_prune_access_log.sql {} --vars {retention: $"($env.ACCESS_LOG_RETENTION_DAYS) days"}

    log info 'Backup completed'

    try {
        for file in ($plan.dumps | get file) {
            rm --recursive --force $file
        }
    } catch {|err| log warning $'Backup dump cleanup skipped: error=($err.msg)' }
}
