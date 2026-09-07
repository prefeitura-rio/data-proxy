#!/usr/bin/env nu

use std/log

let config = open $env.SYNC_CONFIG_PATH
let dsn = $env.PG_DSN
let protected = ["freshness" "access_policy"]

$env.AWS_ACCESS_KEY_ID = $env.GCS_KEY_ID
$env.AWS_SECRET_ACCESS_KEY = $env.GCS_SECRET_KEY

def psql [query: string, --tuples-only] {
    try {
        if $tuples_only {
            ^psql $dsn --no-psqlrc --quiet -t -A -c $query
        } else {
            ^psql $dsn --no-psqlrc --quiet -c $query
        }
    } catch {|err| log error $"psql failed: ($err.msg)" }
}

def redis-del [...keys: string] {
    try {
        ^redis-cli -a $env.REDIS_PASSWORD --no-auth-warning -h $env.REDIS_HOST DEL ...$keys
        | str trim
    } catch {|err|
        log error $"redis-cli failed: ($err.msg)"
        "0"
    }
}

def gcs-rm [table: string] {
    try {
        ^aws s3 rm $"s3://($env.GCS_BUCKET)/($table)" --recursive --endpoint-url $env.GCS_ENDPOINT_URL --quiet
    } catch {|err| log error $"aws s3 rm failed: ($err.msg)" }
}

log info "Cleanup started"

$config.schemas
| columns
| each {|schema|
    let configured = $config.schemas
      | get $schema
      | get tables
      | each {|t| $t.name | split row "." | last }

    let all = psql --tuples-only $"SELECT tablename FROM pg_tables WHERE schemaname = '($schema)'"
      | lines
      | str trim

    let stale = $all
      | where { $in not-in $configured and $in not-in $protected }

    if ($stale | is-empty) {
        log info $"No stale tables in ($schema)"
    } else {
        log info $"Found ($stale | length) stale tables in ($schema)"

        $stale
        | each {|table|
            let full_name = $"($schema).($table)"

            log info $"Dropping table ($full_name)"
            psql $"DROP TABLE IF EXISTS ($schema).\"($table)\" CASCADE"

            log info $"Deleting freshness rows for ($full_name)"
            psql $"DELETE FROM ($schema).freshness WHERE \"table\" = '($table)'"

            log info $"Deleting Redis state for ($full_name)"
            let deleted = redis-del $"dp:state:($full_name)" $"dp:sync:partitions:($full_name)" $"dp:sync:state:($full_name)"

            log info $"Deleted ($deleted) Redis keys for ($full_name)"

            log info $"Deleting GCS objects for ($full_name)"
            gcs-rm $table
        }

        log info $"Truncating access_policy for ($schema)"
        psql $"TRUNCATE ($schema).access_policy"
    }

    log info $"Notifying PostgREST to reload schema cache for ($schema)"
    psql "NOTIFY pgrst"
  }

log info "Cleanup completed"
