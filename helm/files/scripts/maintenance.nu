use std/log
use ./lib.nu [
    execute-sql
    quote-pg
    schema-list
    sync-config
]

def main []: nothing -> nothing {
    log info 'Database maintenance started'

    let config = sync-config

    for schema in ($config | schema-list) {
        execute-sql postgres/call_cleanup_stale_objects.sql {
            schema: (quote-pg ($env.DBOS_APP_SCHEMA? | default data_proxy) identifier)
            schema_argument: (quote-pg $schema literal)
        } --vars {config: ($config | to json)}
    }

    log info 'Database maintenance completed'
}
