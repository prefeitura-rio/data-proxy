#!/usr/bin/env nu
# nu-lint-ignore-file: dont_mix_different_effects

use std/log
use ./lib.nu [quote-pg]

def main []: nothing -> nothing {
    let procedure_schema = quote-pg ($env.DBOS_APP_SCHEMA? | default data_proxy) identifier
    let config = try {
        open $env.SYNC_CONFIG_PATH | to json
    } catch {|err| error make {
        msg: $'Failed to read sync config: ($err.msg)'
        label: retention
    } }
    let scope = $env.SCHEMA?
    let schema_argument = if $scope == null {
        "NULL"
    } else {
        quote-pg $scope literal
    }
    let query = [
        $"CALL ($procedure_schema).apply_retention\("
        ":'config'::jsonb, "
        $schema_argument
        ");"
    ] | str join

    try {
        psql --no-psqlrc --quiet -v ON_ERROR_STOP=1 --set $"config=($config)" -c $query
    } catch {|err| error make {
        msg: $'PostgreSQL retention call failed: ($err.msg)'
        label: {
            text: retention
            span: (metadata $query).span
        }
    } }

    log info 'Retention completed'
}
