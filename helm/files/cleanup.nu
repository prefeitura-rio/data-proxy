#!/usr/bin/env nu
# nu-lint-ignore-file: dont_mix_different_effects

use std/log
use ./lib.nu render-sql

let config = try { open $env.SYNC_CONFIG_PATH } catch {|err| error make {msg: $'Failed to open sync config: ($err.msg)', label: cleanup} }

let dsn = $env.PG_DSN
let protected = [freshness access_policy]

# Return the list of schemas to process, filtered by SCHEMA env var when set.
def schema-list []: nothing -> list<string> {
    let all = $config.schemas | columns
    let target = $env.SCHEMA?

    if $target == null or ($target | is-empty) {
        $all
    } else {
        $all | where $it == $target
    }
}

load-env {
    AWS_ACCESS_KEY_ID: $env.S3_ACCESS_KEY
    AWS_SECRET_ACCESS_KEY: $env.S3_SECRET_KEY
}

# Execute a SQL statement against PostgreSQL
def quote-pg-identifier [value: string]: nothing -> string {
    let escaped = $value | str replace --all '"' '""'
    $'"($escaped)"'
}

def quote-pg-literal [value: string]: nothing -> string {
    let escaped = $value | str replace --all "'" "''"
    $"'($escaped)'"
}

# Execute a SQL statement against PostgreSQL
def postgres [query: string, --tuples-only]: nothing -> string {
    try {
        if $tuples_only {
            psql $dsn --no-psqlrc --quiet -t -A -c $query
        } else {
            psql $dsn --no-psqlrc --quiet -c $query
        }
    } catch {|err| log error $'psql failed: ($err.msg)' }
}

# Delete Redis keys and return the count removed
def redis-del [...keys: string]: nothing -> string {
    try {
        redis-cli -u $env.REDIS_WRITE_URL --no-auth-warning DEL ...$keys
        | str trim
    } catch {|err|
        log error $'redis-cli failed: ($err.msg)'
        '0'
    }
}

log info 'Cleanup started'

for schema in (schema-list) {
    let configured = (
        $config.schemas
        | get --optional $schema
        | get tables
        | each {|t| $t.name | split row . | last }
    )

    let schema_literal = quote-pg-literal $schema
    let all = (
        postgres --tuples-only (render-sql cleanup_list_tables.sql {
            schema: $schema_literal
        })
        | lines
        | str trim
    )

    let stale = (
        $all
        | where $it not-in $configured and $it not-in $protected
    )

    if ($stale | is-empty) {
        log info $'No stale tables in ($schema)'
    } else {
        log info $'Found ($stale | length) stale tables in ($schema)'

        for table in $stale {
            let full_name = $'($schema).($table)'

            log info $'Dropping table ($full_name)'
            (postgres (render-sql cleanup_drop_table.sql {
                schema: (quote-pg-identifier $schema)
                table: (quote-pg-identifier $table)
            }))

            log info $'Deleting freshness rows for ($full_name)'
            (postgres (render-sql cleanup_delete_freshness.sql {
                schema: (quote-pg-identifier $schema)
                table: (quote-pg-literal $table)
            }))

            log info $'Deleting Redis state for ($full_name)'
            let deleted = (redis-del
                $'dp:state:($full_name)'
                $'dp:sync:partitions:($full_name)'
                $'dp:sync:state:($full_name)'
            )

            log info $'Deleted ($deleted) Redis keys for ($full_name)'
        }

        log info $'Truncating access_policy for ($schema)'
        (postgres (render-sql cleanup_truncate_access_policy.sql {
            schema: (quote-pg-identifier $schema)
        }))
    }

    log info $'Notifying PostgREST to reload schema cache for ($schema)'
    postgres 'NOTIFY pgrst'
}

log info 'Cleanup completed'
