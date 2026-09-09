#!/usr/bin/env nu
# nu-lint-ignore-file: dont_mix_different_effects

use std/log

let config = try { open $env.SYNC_CONFIG_PATH } catch {|err| error make {msg: $'Failed to open sync config: ($err.msg)', label: cleanup} }
let dsn = $env.PG_DSN
let protected = [freshness access_policy]

load-env {
    AWS_ACCESS_KEY_ID: $env.GCS_KEY_ID
    AWS_SECRET_ACCESS_KEY: $env.GCS_SECRET_KEY
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
        redis-cli -a $env.REDIS_PASSWORD --no-auth-warning -h $env.REDIS_HOST DEL ...$keys
        | str trim
    } catch {|err|
        log error $'redis-cli failed: ($err.msg)'
        '0'
    }
}

# Recursively delete GCS objects for a table prefix
def gcs-rm [table: string]: nothing -> nothing {
    try {
        aws s3 rm $'s3://($env.GCS_BUCKET)/($table)' --recursive --endpoint-url $env.GCS_ENDPOINT_URL --quiet
    } catch {|err| log error $'aws s3 rm failed: ($err.msg)' }
}

log info 'Cleanup started'

for schema in ($config.schemas | columns) {
    let configured = $config.schemas
    | get --optional $schema
    | get tables
    | each {|t| $t.name | split row . | last }

    let all = postgres --tuples-only $"SELECT tablename FROM pg_tables WHERE schemaname = '($schema)'"
    | lines
    | str trim

    let stale = $all
    | where $it not-in $configured and $it not-in $protected

    if ($stale | is-empty) {
        log info $'No stale tables in ($schema)'
    } else {
        log info $'Found ($stale | length) stale tables in ($schema)'

        for table in $stale {
            let full_name = $'($schema).($table)'

            log info $'Dropping table ($full_name)'
            postgres $'DROP TABLE IF EXISTS ($schema)."($table)" CASCADE'

            log info $'Deleting freshness rows for ($full_name)'
            postgres $"DELETE FROM ($schema).freshness WHERE \"table\" = '($table)'"

            log info $'Deleting Redis state for ($full_name)'
            let deleted = (redis-del
                $'dp:state:($full_name)'
                $'dp:sync:partitions:($full_name)'
                $'dp:sync:state:($full_name)'
            )

            log info $'Deleted ($deleted) Redis keys for ($full_name)'

            log info $'Deleting GCS objects for ($full_name)'
            gcs-rm $table
        }

        log info $'Truncating access_policy for ($schema)'
        postgres $'TRUNCATE ($schema).access_policy'
    }

    log info $'Notifying PostgREST to reload schema cache for ($schema)'
    postgres 'NOTIFY pgrst'
}

log info 'Cleanup completed'
