# nu-lint-ignore-file: dont_mix_different_effects

use std/log
use ./lib.nu [quote-pg render-sql execute-sql fail schema-list]

# Run DuckLake snapshot and file maintenance for one schema.
def ducklake-maintenance [schema: string]: any -> string {
    let bucket = $env.S3_BUCKET
    let prefix = $env.DUCKLAKE_CATALOG_PATH? | default ducklake
    let catalog_local_path = $env.DUCKLAKE_CATALOG_LOCAL_PATH? | default /var/lib/ducklake/catalogs

    let setup = render-sql duckdb/setup.sql {
        s3_key_id: $env.S3_ACCESS_KEY
        s3_secret_key: $env.S3_SECRET_KEY
        s3_endpoint: $env.S3_ENDPOINT
        s3_use_ssl: ($env.S3_USE_SSL? | default false)
    }

    let attach = render-sql duckdb/attach.sql {
        catalog: $"'ducklake:sqlite:($catalog_local_path)/($schema)/catalog.sqlite'"
        data_path: $"'s3://($bucket)/($prefix)/($schema)'"
        encrypted: ($env.DUCKLAKE_ENCRYPTED? | default false)
    }

    let interval = ($env.DUCKLAKE_SNAPSHOT_EXPIRATION? | default 7d) | str replace --regex d$ ''
    let maintenance = render-sql duckdb/maintenance.sql {
        interval: $'($interval) days'
        max_compacted_files: ($env.DUCKLAKE_MAX_COMPACTED_FILES? | default 10)
        rewrite_delete_threshold: ($env.DUCKLAKE_REWRITE_DELETE_THRESHOLD? | default 0.95)
    }

    let sql = [$setup $attach $maintenance] | str join "\n"

    try {
        $sql | duckdb --newline \n
    } catch {|err| fail $'DuckLake maintenance failed for schema ($schema): ($err.msg)' {
        command: ducklake-maintenance
        span: (metadata $schema).span
    } }
}

def main []: nothing -> nothing {
    log info 'Maintenance started'

    let config = try { open $env.SYNC_CONFIG_PATH } catch {|err| fail $'Failed to open sync config: ($err.msg)' {
        command: maintenance
        span: (metadata $env.SYNC_CONFIG_PATH).span
    } }

    for schema in (schema-list $config) {
        execute-sql postgres/call_cleanup_stale_objects.sql {
            schema: (quote-pg ($env.DBOS_APP_SCHEMA? | default data_proxy) identifier)
            schema_argument: (quote-pg $schema literal)
        } --vars {config: ($config | to json)}

        ducklake-maintenance $schema
    }

    log info 'Maintenance completed'
}
