#!/usr/bin/env nu

loop {
    let result = psql --no-psqlrc --quiet -t -A -c "SELECT 1" | complete
    if $result.exit_code == 0 { break }
    sleep 2sec
}
