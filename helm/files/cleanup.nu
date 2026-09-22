#!/usr/bin/env nu
# nu-lint-ignore-file: dont_mix_different_effects

use std/log
use ./lib.nu [quote-pg]

# Call one application database maintenance procedure.
def postgres [call: record<procedure: string, config: string, schema_argument: string>]: nothing -> string {
    let query = [
        $"CALL ($call.procedure)\("
        ":'config'::jsonb, "
        $call.schema_argument
        ");"
    ] | str join

    try {
        psql --no-psqlrc --quiet -v ON_ERROR_STOP=1 --set $"config=($call.config)" -c $query
    } catch {|err| error make {
        msg: $'PostgreSQL maintenance call failed: ($err.msg)'
        label: {
            text: postgres
            span: (metadata $call.procedure).span
        }
    } }
}

def main []: nothing -> nothing {
    let procedure_schema = quote-pg ($env.DBOS_APP_SCHEMA? | default data_proxy) identifier
    let config = try {
        open $env.SYNC_CONFIG_PATH | to json
    } catch {|err| error make {
        msg: $'Failed to read sync config: ($err.msg)'
        label: cleanup
    } }
    let scope = $env.SCHEMA?
    let schema_argument = if $scope == null {
        "NULL"
    } else {
        quote-pg $scope literal
    }

    log info 'Application cleanup started'
    postgres {
        procedure: $'($procedure_schema).cleanup_stale_objects'
        config: $config
        schema_argument: $schema_argument
    } | ignore
    log info 'Application cleanup completed'

    log info 'DBOS state cleanup started'
    let state_query = [$"CALL ($procedure_schema).cleanup_table_state\(" ":'config'::jsonb);"] | str join
    try {
        psql $env.DBOS_SYSTEM_DATABASE_URL --no-psqlrc --quiet -v ON_ERROR_STOP=1 --set $"config=($config)" -c $state_query
    } catch {|err| error make {
        msg: $'PostgreSQL state cleanup call failed: ($err.msg)'
        label: {
            text: cleanup
            span: (metadata $state_query).span
        }
    } }
    log info 'DBOS state cleanup completed'
}
