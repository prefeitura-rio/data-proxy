#!/usr/bin/env nu

use std/log
use ./lib.nu [fail schema-list sync-config]

# Create one catalog directory or fail with contextual logging.
def ensure-directory [path: path]: nothing -> nothing {
    try {
        mkdir $path
    } catch {|err| fail $'Catalog directory creation failed path=($path) error=($err.msg)' {
            command: ensure-directory
            span: (metadata $path).span
        } }
}

# Change catalog file permissions or fail with contextual logging.
def set-permissions [mode: string, ...paths: string]: nothing -> nothing {
    let result = chmod $mode ...$paths | complete

    if $result.exit_code != 0 {
        fail $'Catalog permission change failed mode=($mode) paths=($paths | str join ",") error=($result.stderr | str trim)' {
            command: set-permissions
            span: (metadata $mode).span
        }
    }
}

# Restore one reader catalog and follow its Litestream replica.
def restore-reader-schema [schema: string, base: string]: nothing -> nothing {
    let dir = $'($base)/($schema)'
    let db = $'($dir)/catalog.sqlite'
    let txid = $'($db)-txid'

    ensure-directory $dir
    set-permissions '777' $dir
    log info $'Reader catalog restore started schema=($schema) db=($db)'

    if ($db | path exists) and not ($txid | path exists) {
        log info $'Reader catalog restore removing incomplete files schema=($schema)'
        try {
            rm --force $db $'($db)-wal' $'($db)-shm'
        } catch {|err| fail $'Incomplete catalog removal failed schema=($schema) error=($err.msg)' {
                command: restore-reader-schema
                span: (metadata $db).span
            } }
    }

    let result = if ($db | path exists) {
        set-permissions '666' $db $txid
        log info $'Reader catalog restore overwriting local catalog schema=($schema)'
        litestream restore -f -config /projected/litestream-read.yaml $db | complete
    } else {
        log info $'Reader catalog restore creating local catalog schema=($schema)'
        litestream restore -if-replica-exists -f -config /projected/litestream-read.yaml $db | complete
    }

    if $result.exit_code != 0 {
        let error = $result.stderr | str trim

        if ($error =~ 'saved TXID') and ($error =~ 'ahead of latest snapshot') {
            log warning $'Reader catalog restore removing TXID ahead of replica schema=($schema)'
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

    log info $'Reader catalog restore completed schema=($schema)'
}

# Prepare writer catalog directories and start Litestream replication.
def replicate-writer [schemas: list<string>, base: string]: nothing -> nothing {
    for schema in $schemas {
        let dir = $'($base)/($schema)'
        ensure-directory $dir
        set-permissions '777' $dir
    }

    log info $'Writer catalog replication started schemas=($schemas | length)'
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

    if $read {
        let base = $env.DUCKLAKE_CATALOG_LOCAL_PATH
        let secrets = $'($base)/.duckdb/stored_secrets'
        ensure-directory $secrets
        set-permissions '777' $secrets
        log info $'Reader catalog recovery started schemas=($schemas | length)'

        $schemas | par-each {|schema|
            loop {
                try {
                    restore-reader-schema $schema $base
                    break
                } catch {|err|
                    log error $'Reader catalog restore failed schema=($schema) error=($err.msg); retrying in 5s'
                    sleep 5sec
                }
            }
        }

        log info 'Reader catalog recovery completed'
        return
    }

    replicate-writer $schemas $env.DUCKLAKE_CATALOG_WRITER_PATH
}
