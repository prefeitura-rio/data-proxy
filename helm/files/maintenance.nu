# nu-lint-ignore-file: dont_mix_different_effects

use std/log
use ./lib.nu [quote-pg render-sql schema-list]

# Call one application database maintenance procedure.
def postgres-cleanup [config: string, schema_argument: string]: nothing -> nothing {
    let procedure_schema = quote-pg ($env.DBOS_APP_SCHEMA? | default data_proxy) identifier
    let query = [
        $"CALL ($procedure_schema).cleanup_stale_objects\("
        ":'config'::jsonb, "
        $schema_argument
        ");"
    ] | str join

    try {
        psql --no-psqlrc --quiet -v ON_ERROR_STOP=1 --set $"config=($config)" -c $query
    } catch {|err| error make {
        msg: $'PostgreSQL cleanup call failed: ($err.msg)'
        label: {
            text: postgres-cleanup
            span: (metadata $config).span
        }
    } }
}

# Run DuckLake snapshot and file maintenance for one schema.
def ducklake-maintenance [schema: string]: any -> string {
    let bucket = $env.S3_BUCKET
    let prefix = $env.DUCKLAKE_CATALOG_PATH? | default ducklake
    let catalog_local_path = $env.DUCKLAKE_CATALOG_LOCAL_PATH? | default /var/lib/ducklake/catalogs
    let catalog = $"'ducklake:sqlite:($catalog_local_path)/($schema)/catalog.sqlite'"
    let data_path = $"'s3://($bucket)/($prefix)/($schema)'"

    let setup = render-sql duckdb/setup.sql {
        s3_key_id: $env.S3_ACCESS_KEY
        s3_secret_key: $env.S3_SECRET_KEY
        s3_endpoint: $env.S3_ENDPOINT
        s3_use_ssl: ($env.S3_USE_SSL? | default false)
    }

    let attach = render-sql duckdb/attach.sql {
        catalog: $catalog
        data_path: $data_path
        encrypted: $encrypted
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
    } catch {|err| error make {
        msg: $'DuckLake maintenance failed for schema ($schema): ($err.msg)'
        label: {
            text: ducklake-maintenance
            span: (metadata $schema).span
        }
    } }
}
        ducklake-maintenance $schema
    }
    log info 'Maintenance completed'
}
