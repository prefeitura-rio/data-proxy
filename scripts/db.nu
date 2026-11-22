use lib.nu [
    dbos-migrate
    fail
    goose-up
    render-template
    run-sql
    test-url
]

const DUCKDB_SETTINGS = [
    'duckdb.unsafe_allow_execution_inside_functions'
    'duckdb.unsafe_allow_mixed_transactions'
]

# Prepare test databases with migrations, request roles, access rules and persistent DuckDB secrets.
def main []: nothing -> nothing {
    let port = $env.TEST_POSTGRES_PORT
    let templates = $env.DEVENV_ROOT | path join sync tests templates postgres
    $env.PGPASSWORD = 'test'

    let databases = [test_template test]
    | each {|database|
        $DUCKDB_SETTINGS | each {|name| $"ALTER DATABASE ($database) SET ($name) = 'on';" }
    }
    | flatten
    | prepend [
        'CREATE DATABASE test_template;'
        'CREATE DATABASE dbos_system;'
        'CREATE DATABASE ducklake;'
    ]
    | str join (char nl)
    $databases | run-sql (test-url postgres $port)

    render-template ($templates | path join fixture.sql) | run-sql (test-url test_template $port)

    let rules_text = open --raw ($env.DEVENV_ROOT | path join helm files config rbac.toml) | decode utf-8
    let rbac_sql = render-template ($env.DEVENV_ROOT | path join helm files templates postgres load_rbac.sql) {
        rules: ($rules_text | from toml)
        anonymous_role: '"anon"'
        source_hash: ($rules_text | hash sha256)
    }
    for database in ['test_template' 'test'] {
        let url = test-url $database $port
        dbos-migrate $url
        goose-up console $url
        $rbac_sql | run-sql $url
        'DELETE FROM dbos.workflow_status;' | run-sql $url
    }
    goose-up ducklake (test-url ducklake $port)

    render-template ($templates | path join create_ducklake_catalog_secret.sql) {
        host: localhost
        port: $port
        database: ducklake
        user: test
        password: test
    } | run-sql (test-url test $port)
}

# Generate one Mermaid diagram with tbls and report either error stream on failure.
def run-tbls [config: record, viewpoint: int, file: path]: nothing -> nothing {
    let result = $config | to yaml | tbls out --config /dev/stdin --format mermaid --viewpoint $viewpoint --out $file | complete

    if $result.exit_code != 0 {
        let reason = [$result.stdout $result.stderr] | each { str trim } | where $it != '' | str join ' '

        fail $'tbls for ($file | path basename) failed: ($reason). Run `devenv --profile docs tasks run docs:build:model`, which starts the databases and stops them.'
    }
}

# Generate the owned-table diagrams from the configured database connections.
def "main diagrams" []: nothing -> nothing {
    let configs = open ($env.DEVENV_ROOT | path join docs .tbls.yaml)

    for config in ($configs | values) {
        for entry in ($config.viewpoints | enumerate) {
            let file = $env.DEVENV_ROOT | path join docs diagrams $'($entry.item.name).mmd'

            run-tbls $config $entry.index $file
            $'%% ($entry.item.desc)(char nl)((open --raw $file | decode utf-8) | str trim)(char nl)' | save --force $file
        }
    }
}
