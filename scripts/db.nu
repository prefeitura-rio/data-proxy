use lib.nu [fail]

const CATALOG_DIR = '/var/lib/postgresql/ducklake'

const DUCKDB_SETTINGS = [
    'duckdb.unsafe_allow_execution_inside_functions'
    'duckdb.unsafe_allow_mixed_transactions'
]

# Render one SQL template with minijinja-cli and return the SQL.
def render [template: path, ...variables: string]: nothing -> string {
    let defines = $variables | each {|variable| ['-D' $variable] } | flatten
    let result = minijinja-cli ...$defines $template | complete

    if $result.exit_code != 0 {
        fail $'Could not render ($template): ($result.stderr)' {
            command: render
            span: (metadata $template).span
        }
    }

    $result.stdout
}

# Run one SQL script in the PostgreSQL test server and stop at the first error.
def run-sql [database: string, port: string]: string -> nothing {
    let result = $in
    | psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 --host=127.0.0.1 $'--port=($port)' --username=test $'--dbname=($database)'
    | complete

    if $result.exit_code != 0 {
        fail $'psql failed in ($database): ($result.stderr)' {
            command: run-sql
            span: (metadata $database).span
        }
    }
}

# Create the DuckLake catalog directory and the test databases, and put the PostgREST schema in `test`.
def main []: nothing -> nothing {
    let port = $env.TEST_POSTGRES_PORT
    let container = $env.TEST_POSTGRES_CONTAINER
    let templates = $env.DEVENV_ROOT | path join sync tests templates postgres
    let filter = $env.DEVENV_ROOT | path join sync src sync templates postgres views routing filter.sql
    let pushdown = $templates | path join pushdown.sql
    $env.PGPASSWORD = 'test'

    let runtime = $env.CONTAINER_RUNTIME? | default 'podman'
    ^$runtime exec $container mkdir -p $CATALOG_DIR

    let databases = ['test_template' 'test']
    | each {|database|
        $DUCKDB_SETTINGS | each {|name| $"ALTER DATABASE ($database) SET ($name) = 'on';" }
    }
    | flatten
    | prepend ['CREATE DATABASE test_template;' 'CREATE DATABASE dbos_system;']
    | str join (char nl)
    $databases | run-sql postgres $port

    render ($templates | path join fixture.sql) | run-sql test_template $port

    'CREATE EXTENSION IF NOT EXISTS pg_duckdb; CREATE SCHEMA IF NOT EXISTS data_proxy;' | run-sql test $port
    render $filter 'schema="data_proxy"' | run-sql test $port

    let pushdown_sql = try {
        open --raw $pushdown
    } catch {|err| fail $'Could not read ($pushdown): ($err.msg)' {
            command: main
            span: (metadata $pushdown).span
        } }
    $pushdown_sql | run-sql test $port
}
