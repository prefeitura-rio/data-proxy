#!/usr/bin/env nu
# nu-lint-ignore-file: dont_mix_different_effects, string_may_be_bare

use std/log
use ./lib.nu [schema-list]

# Restore one schema's SQLite DuckLake catalog from the Litestream replica in S3.
def restore-schema [schema: string, base: string]: nothing -> nothing {
    let dir = $'($base)/($schema)'
    let db = $'($dir)/catalog.sqlite'
    let txid = $'($db)-txid'

    try { mkdir $dir } catch { null }
    try { chmod 777 $dir } catch { null }
    log info $'Catalog restore started schema=($schema) db=($db)'

    if ($db | path exists) and not ($txid | path exists) {
        try { rm --force $db $'($db)-wal' $'($db)-shm' } catch { null }
    }

    if ($db | path exists) {
        try { chmod 666 $db $txid } catch { null }
        litestream restore -f -config /projected/litestream-read.yaml $db
    } else {
        litestream restore -if-replica-exists -f -config /projected/litestream-read.yaml $db
    }

    log info $'Catalog restore completed schema=($schema)'
}

def main []: nothing -> nothing {
    let base = $env.DUCKLAKE_CATALOG_LOCAL_PATH? | default '/var/lib/ducklake/catalogs'
    let secrets = $'($base)/.duckdb/stored_secrets'
    try { mkdir $secrets } catch { null }
    try { chmod 777 $secrets } catch { null }

    let config = try {
        open --raw $env.SYNC_CONFIG_PATH | from json
    } catch {
        fail $'Could not read sync config: ($env.SYNC_CONFIG_PATH)'
    }

    let schemas = schema-list $config

    log info $'Catalog recovery started schemas=($schemas | length)'

    $schemas | par-each {|schema|
        loop {
            try {
                restore-schema $schema $base
                break
            } catch {|err|
                log error $'Catalog restore failed schema=($schema) error=($err.msg)'
                sleep 5sec
            }
        }
    }

    log info 'Catalog recovery completed'
}
