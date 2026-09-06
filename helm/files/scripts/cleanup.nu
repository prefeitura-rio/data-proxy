#!/usr/bin/env nu

use std/log

let config = open $env.SYNC_CONFIG_PATH
let dsn = $env.PG_DSN
let protected = ["freshness" "access_policy"]

$env.AWS_ACCESS_KEY_ID = $env.GCS_KEY_ID
$env.AWS_SECRET_ACCESS_KEY = $env.GCS_SECRET_KEY

log info "Cleanup started"

$config.schemas
| columns
| each {|schema|
    let configured = $config.schemas
      | get $schema
      | get tables
      | each {|t| $t.name | split row "." | last }

    let all = ^psql $dsn --no-psqlrc --quiet -t -A -c $"SELECT tablename FROM pg_tables WHERE schemaname = '\''($schema)'\''"
      | lines
      | str trim

    let stale = $all
      | where { $in not-in $configured and $in not-in $protected }

    if ($stale | is-empty) {
        log info $"No stale tables in ($schema)"
    } else {
        log info $"Found ($stale | length) stale table(s) in ($schema)"

        $stale
        | each {|table|
            let full_name = $"($schema).($table)"

            log info $"Dropping table ($full_name)"
            ^psql $dsn --no-psqlrc --quiet -c $"DROP TABLE IF EXISTS ($schema).\\\"($table)\\\" CASCADE"

            log info $"Deleting freshness rows for ($full_name)"
            ^psql $dsn --no-psqlrc --quiet -c $"DELETE FROM ($schema).freshness WHERE \\\"table\\\" = '\''($table)'\''"

            log info $"Deleting Redis state for ($full_name)"
            let deleted = ^redis-cli -u $env.REDIS_URL DEL $"dp:state:($full_name)" $"dp:sync:partitions:($full_name)" $"dp:sync:state:($full_name)"
              | str trim
            log info $"Deleted ($deleted) Redis key(s) for ($full_name)"

            log info $"Deleting GCS objects for ($full_name)"
            ^aws s3 rm $"s3://($env.GCS_BUCKET)/($table)" --recursive --endpoint-url $env.GCS_ENDPOINT_URL --quiet
        }

        log info $"Truncating access_policy for ($schema)"
        ^psql $dsn --no-psqlrc --quiet -c $"TRUNCATE ($schema).access_policy"
    }

    log info $"Notifying PostgREST to reload schema cache for ($schema)"
    ^psql $dsn --no-psqlrc --quiet -c "NOTIFY pgrst"
  }

log info "Cleanup completed"
