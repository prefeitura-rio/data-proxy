#!/usr/bin/env nu
# nu-lint-ignore-file: dont_mix_different_effects

use std/log
use ./lib.nu [quote-pg render-sql]

$env.SQL_TEMPLATE_DIR = '/scripts'

let config = try { open $env.SYNC_CONFIG_PATH } catch {|err| error make {msg: $'Failed to open sync config: ($err.msg)', label: retention} }

let writers = try {
    $env.SCHEMA_WRITERS | from json | get writers
} catch {|err| error make {msg: $'Failed to read schema writers: ($err.msg)', label: retention} }

# Execute a SQL statement against one PostgreSQL DSN and return its output.
def postgres [dsn: string, query: string]: nothing -> string {
    try {
        psql $dsn --no-psqlrc --quiet -c $query | str trim
    } catch {|err| log error $'psql failed: ($err.msg)' }
}

log info 'Retention started'

for schema in ($config.schemas | columns) {
    let dsn = $writers | get --optional $schema

    if $dsn == null {
        log warning $'No writer DSN for schema ($schema), skipping'
        continue
    }

    for table in ($config.schemas | get --optional $schema | get tables) {
        let retention = $table | get --optional retention

        if $retention == null {
            continue
        }

        let table_name = $table.name | split row . | last
        let full_name = $'($schema).($table_name)'

        let query = render-sql retention_delete.sql {
            schema: (quote-pg $schema identifier)
            table: (quote-pg $table_name identifier)
            column: (quote-pg $retention.column identifier)
            window: (quote-pg $retention.window literal)
        }

        log info $'Deleting expired rows from ($full_name)'
        postgres $dsn $query
        log info $'Retention applied to ($full_name)'
    }
}

log info 'Retention completed'
