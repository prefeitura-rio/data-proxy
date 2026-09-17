#!/usr/bin/env nu

use std/log

let timeout = 10min
let deadline = (date now) + $timeout
log info $"Waiting for authenticated PostgreSQL access as ($env.PGUSER) at ($env.PGHOST):($env.PGPORT)..."

loop {
    if (date now) >= $deadline {
        log error $"Timed out after ($timeout) waiting for authenticated PostgreSQL access"
        exit 1
    }
    let result = psql --no-psqlrc --quiet -t -A -c "SELECT 1" | complete

    if $result.exit_code == 0 {
        log info "Authenticated PostgreSQL access is ready"
        break
    }

    log warning $"PostgreSQL authentication is not ready: (($result.stderr | str trim))"
    sleep 2sec
}
