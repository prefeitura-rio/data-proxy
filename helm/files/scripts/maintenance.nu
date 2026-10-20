use std/log
use ./lib.nu [
    app-schema
    execute-sql
    quote-literal
    schema-list
    sync-config
]

# Clean the stale objects of every schema in the configuration.
def main []: nothing -> nothing {
    log info 'Database maintenance started'

    let config = sync-config

    for schema in ($config | schema-list) {
        execute-sql postgres/call_cleanup_stale_objects.sql {
            schema: (app-schema)
            schema_argument: (quote-literal $schema)
        } --vars {config: ($config | to json)}
    }

    log info 'Database maintenance completed'
}
