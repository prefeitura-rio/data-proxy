#!/usr/bin/env nu

use std/log
use ./lib.nu [fail schema-list sync-config]

# Create one catalog directory or fail with contextual logging.
def ensure-directory [path: path]: nothing -> nothing {
    log info $'Catalog directory creation started: path=($path)'

    try {
        mkdir $path
    } catch {|err| fail $'Catalog directory creation failed path=($path) error=($err.msg)' {
            command: ensure-directory
            span: (metadata $path).span
        } }
}

# Change catalog file permissions or fail with contextual logging.
def set-permissions [mode: string, ...paths: string]: nothing -> nothing {
    log info $'Catalog permission update started: mode=($mode) paths=($paths | str join ",")'
    let result = chmod $mode ...$paths | complete

    if $result.exit_code != 0 {
        fail $'Catalog permission change failed mode=($mode) paths=($paths | str join ",") error=($result.stderr | str trim)' {
            command: set-permissions
            span: (metadata $mode).span
        }
    }
}

# Restore one reader catalog and follow its Litestream replica.
def restore-reader-schema [schema: string, base: string, attempt: int]: nothing -> nothing {
    let dir = $'($base)/($schema)'
    let db = $'($dir)/catalog.sqlite'
    let txid = $'($db)-txid'

    ensure-directory $dir
    set-permissions '777' $dir
    log info $'Catalog reader restoration started: schema=($schema) attempt=($attempt) catalog=($db)'

    if ($db | path exists) and not ($txid | path exists) {
        log warning $'Catalog reader incomplete catalog removal started: schema=($schema) catalog=($db)'
        try {
            rm --force $db $'($db)-wal' $'($db)-shm'
        } catch {|err| fail $'Incomplete catalog removal failed schema=($schema) error=($err.msg)' {
                command: restore-reader-schema
                span: (metadata $db).span
            } }
    }

    let result = if ($db | path exists) {
        set-permissions '666' $db $txid
        log info $'Catalog reader restoration is overwriting: schema=($schema) catalog=($db)'
        litestream restore -f -config /projected/litestream-read.yaml $db | complete
    } else {
        log info $'Catalog reader restoration is creating: schema=($schema) catalog=($db)'
        litestream restore -if-replica-exists -f -config /projected/litestream-read.yaml $db | complete
    }

    if $result.exit_code != 0 {
        let error = $result.stderr | str trim

        if ($error =~ 'saved TXID') and ($error =~ 'ahead of latest snapshot') {
            log warning $'Catalog reader stale transaction removal started: schema=($schema) catalog=($db)'
            try {
                rm --force $db $txid $'($db)-wal' $'($db)-shm'
            } catch {|err| fail $'Stale catalog removal failed schema=($schema) error=($err.msg)' {
                    command: restore-reader-schema
                    span: (metadata $db).span
                } }
        }

        fail $'Reader catalog restore command failed schema=($schema) error=($error)' {
            command: restore-reader-schema
            span: (metadata $db).span
        }
    }

    log info $'Catalog reader restoration completed: schema=($schema) catalog=($db)'
}

# Prepare writer catalog directories and start Litestream replication.
def replicate-writer [schemas: list<string>, base: string]: nothing -> nothing {
    for schema in $schemas {
        let dir = $'($base)/($schema)'
        let catalog = $'($dir)/catalog.sqlite'
        ensure-directory $dir
        set-permissions '777' $dir
        log info $'Catalog writer preparation completed: schema=($schema) catalog=($catalog) exists=($catalog | path exists)'
    }

    log info $'Catalog writer replication started: schemas=($schemas | length) config=/projected/litestream-write.yaml'
    litestream replicate -restore-if-db-not-exists -config /projected/litestream-write.yaml
}

# Recover reader catalogs or replicate writer catalogs according to the selected mode.
def main [
    --read # Restore reader catalogs from the S3 replica.
    --write # Restore missing writer catalogs and replicate them to S3.
]: nothing -> nothing {
    if $read == $write {
        fail 'Specify exactly one catalog mode: --read or --write' {
            command: main
            span: (metadata $read).span
        }
    }

    let schemas = sync-config | schema-list

    if not $read {
        log info $'Catalog writer recovery started: schemas=($schemas | length) base=($env.DUCKLAKE_CATALOG_WRITER_PATH)'
        replicate-writer $schemas $env.DUCKLAKE_CATALOG_WRITER_PATH
        return
    }

    let base = $env.DUCKLAKE_CATALOG_LOCAL_PATH
    let timeout = $env.CATALOG_RECOVERY_TIMEOUT | into duration
    let retry = $env.CATALOG_RECOVERY_RETRY | into duration
    let keepalive = $env.CATALOG_RECOVERY_KEEPALIVE | into duration
    let deadline = (date now) + $timeout
    let secrets = $'($base)/duckdb-secrets'

    ensure-directory $secrets
    set-permissions '777' $secrets
    log info $'Catalog reader recovery started: schemas=($schemas | length) base=($base) timeout=($timeout) retry=($retry) keepalive=($keepalive)'

    $schemas | par-each {|schema|
        mut attempt = 0
        mut recovered = false

        loop {
            if not $recovered {
                $attempt += 1
                let current_attempt = $attempt

                try {
                    restore-reader-schema $schema $base $current_attempt
                    $recovered = true
                    log info $'Catalog reader recovery is idle: schema=($schema) keepalive=($keepalive)'
                } catch {|err|
                    if (date now) >= $deadline {
                        fail $'Reader catalog recovery timed out schema=($schema) timeout=($timeout) error=($err.msg)' {
                            command: restore-reader-schema
                            span: (metadata $schema).span
                        }
                    }

                    log warning $'Catalog reader recovery will retry: schema=($schema) attempt=($current_attempt) retry=($retry) error=($err.msg)'
                    sleep $retry
                }
            } else {
                log info $'Catalog reader recovery is keeping alive: schema=($schema) sleep=($keepalive)'
                sleep $keepalive
            }
        }
    }
}
