#!/usr/bin/env nu
# nu-lint-ignore-file: dont_mix_different_effects

use std/log
use ./lib.nu [quote-pg refresh-postgrest render-sql schema-list]

let config = try { open $env.SYNC_CONFIG_PATH } catch {|err| error make {msg: $'Failed to open sync config: ($err.msg)', label: cleanup} }
let protected = [freshness access_policy access_log]

# Return the list of schemas to process, filtered by SCHEMA env var when set.
load-env {
    AWS_ACCESS_KEY_ID: $env.S3_ACCESS_KEY
    AWS_SECRET_ACCESS_KEY: $env.S3_SECRET_KEY
}

# Execute a SQL statement against PostgreSQL.
def postgres [query: string, --tuples-only]: nothing -> string {
    try {
        if $tuples_only {
            psql --no-psqlrc --quiet -t -A -c $query
        } else {
            psql --no-psqlrc --quiet -c $query
        }
    } catch {|err| log error $'psql failed: ($err.msg)' }
}

# Delete Redis keys and return the count removed
def redis-del [...keys: string]: nothing -> string {
    try {
        let url = ($env.REDIS | from json | get write | url parse)
        let dsn = $"redis://default:($url.password)@($url.host):($url.port)($url.path)"
        redis-cli -u $dsn --no-auth-warning DEL ...$keys | str trim
    } catch {|err|
        log error $'redis-cli failed: ($err.msg)'
        '0'
    }
}

log info 'Cleanup started'

for schema in (schema-list $config) {
    let configured = (
        $config.schemas
        | get --optional $schema
        | get tables
        | each {|t| $t.name | split row . | last }
    )

    let configured_fallback = (
        $config.schemas
        | get --optional $schema
        | get tables
        | where ($it.fallback? | default false)
        | each {|t| $t.name | split row . | last }
    )

    let all = (
        postgres --tuples-only (render-sql cleanup_list_tables.sql {
            schema: (quote-pg $schema literal)
        })
        | lines
        | str trim
    )

    let stale = (
        $all
        | where $it not-in $configured and $it not-in $protected
    )

    let stale_fallback = (
        postgres --tuples-only (render-sql cleanup_list_fallback.sql {
            schema: (quote-pg $schema literal)
        })
        | lines
        | str trim
        | where ($it | is-not-empty)
        | where $it not-in $configured_fallback
    )

    if ($stale | is-empty) {
        log info $'No stale tables in ($schema)'
    } else {
        log info $'Found ($stale | length) stale tables in ($schema)'

        for table in $stale {
            let full_name = $'($schema).($table)'

            log info $'Dropping table ($full_name)'
            (postgres (render-sql cleanup_drop_table.sql {
                schema: (quote-pg $schema identifier)
                table: (quote-pg $table literal)
            }))

            log info $'Deleting freshness rows for ($full_name)'
            (postgres (render-sql cleanup_delete_freshness.sql {
                schema: (quote-pg $schema identifier)
                table: (quote-pg $table literal)
            }))

            log info $'Deleting Redis state for ($full_name)'
            let deleted = (redis-del
                $'dp:state:($full_name)'
                $'dp:sync:partitions:($full_name)'
                $'dp:sync:state:($full_name)'
            )

            log info $'Deleted ($deleted) Redis keys for ($full_name)'
        }

        log info $'Deleting access_policy rows for ($schema)'
        (postgres (render-sql cleanup_delete_access_policy.sql {
            schema: (quote-pg $schema identifier)
        }))
    }

    if ($stale_fallback | is-empty) {
        log info $'No stale fallback objects in ($schema)'
    } else {
        log info $'Found ($stale_fallback | length) stale fallback objects in ($schema)'

        for table in $stale_fallback {
            log info $'Dropping fallback objects for ($schema).($table)'
            (postgres (render-sql cleanup_drop_fallback.sql {
                schema: (quote-pg $schema identifier)
                table: (quote-pg $table literal)
            }))
        }
    }

    if not (($stale | is-empty) and ($stale_fallback | is-empty)) {
        log info $'Refreshing PostgREST schema cache for ($schema)'
        refresh-postgrest $env.KUBERNETES_NAMESPACE
    }
}

log info 'Cleanup completed'
