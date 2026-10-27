use lib.nu [check-result parse-toml read-text remove-path write-text]

const DUCKDB_SETTINGS = [
    'duckdb.unsafe_allow_execution_inside_functions'
    'duckdb.unsafe_allow_mixed_transactions'
]

# Render one SQL template with minijinja-cli and a JSON context, and return the SQL.
def render [template: path, context: record = {}]: nothing -> string {
    let file = mktemp --tmpdir --suffix .json

    $context | to json | write-text $file
    let result = minijinja-cli --strict --autoescape none --format json $template $file | complete
    remove-path $file

    $result | check-result $'Rendering of ($template)'
}

# Run one SQL script in the PostgreSQL test server and stop at the first error.
def run-sql [database: string, port: string]: string -> nothing {
    psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 --host=127.0.0.1 $'--port=($port)' --username=test $'--dbname=($database)'
    | complete
    | check-result $'psql in ($database)'
    | ignore
}

# Create the test databases, including the DuckLake catalog database, and the rbac rules that the data schemas call.
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
    $databases | run-sql postgres $port

    render ($templates | path join fixture.sql) | run-sql test_template $port

    # The policies and the functions of every data schema ask rbac.can(), so the data tests need the rules too.
    let rules_text = read-text ($env.DEVENV_ROOT | path join helm files config rbac.toml)
    let rbac_sql = render ($env.DEVENV_ROOT | path join helm files templates postgres create_rbac.sql) {
        rules: ($rules_text | parse-toml)
        anonymous_role: '"anon"'
        source_hash: ($rules_text | hash sha256)
    }
    for database in ['test_template' 'test'] {
        $rbac_sql | run-sql $database $port
    }

    # DuckDB reads persistent secrets once per backend, so create them before any test backend starts.
    render ($templates | path join create_ducklake_catalog_secret.sql) {host: localhost, port: $port, database: ducklake, user: test, password: test} | run-sql test $port

    'CREATE EXTENSION IF NOT EXISTS pg_duckdb;' | run-sql test $port
}
