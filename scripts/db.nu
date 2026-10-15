use lib.nu [fail]

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

# Create the test databases, including the DuckLake catalog database, and put the PostgREST schema in `test`.
def main []: nothing -> nothing {
    let port = $env.TEST_POSTGRES_PORT
    let templates = $env.DEVENV_ROOT | path join sync tests templates postgres
    let filter = $env.DEVENV_ROOT | path join sync src sync templates postgres views routing filter.sql
    let pushdown = $templates | path join pushdown.sql
    $env.PGPASSWORD = 'test'

    let databases = ['test_template' 'test']
    | each {|database|
        $DUCKDB_SETTINGS | each {|name| $"ALTER DATABASE ($database) SET ($name) = 'on';" }
    }
    | flatten
    | prepend ['CREATE DATABASE test_template;' 'CREATE DATABASE dbos_system;' 'CREATE DATABASE ducklake;']
    | str join (char nl)
    $databases | run-sql postgres $port

    render ($templates | path join fixture.sql) | run-sql test_template $port

    # DuckDB reads persistent secrets once per backend, so create them before any test backend starts.
    render ($templates | path join create_ducklake_reader_secret.sql) 'host=localhost' $'port=($port)' 'database=ducklake' 'user=test' 'password=test' | run-sql test $port

    'CREATE EXTENSION IF NOT EXISTS pg_duckdb; CREATE SCHEMA IF NOT EXISTS data_proxy;' | run-sql test $port

    let url = $'postgresql://test:test@127.0.0.1:($port)/test'

    # The admin views read the state tables, which the configure job creates before goose runs.
    render ($env.DEVENV_ROOT | path join helm files templates postgres create_state.sql) 'schema="data_proxy"' | run-sql test $port
    render ($env.DEVENV_ROOT | path join helm files templates postgres create_reload.sql) 'schema="data_proxy"' | run-sql test $port
    render ($env.DEVENV_ROOT | path join helm files templates postgres create_pre_request.sql) 'schema="data_proxy"' | run-sql test $port

    # The chart declares the console permission levels as CNPG managed roles. The test server creates them here.
    [
        'CREATE ROLE console_viewer NOLOGIN'
        'CREATE ROLE console_operator NOLOGIN IN ROLE console_viewer'
        'CREATE ROLE console_editor NOLOGIN IN ROLE console_viewer'
        'CREATE ROLE console_admin NOLOGIN IN ROLE console_operator, console_editor'
    ]
    | str join ";\n"
    | run-sql test $port

    # The admin views read the DBOS tables, so DBOS creates them before goose runs.
    uv run --frozen --project ($env.DEVENV_ROOT | path join sync) dbos migrate --sys-db-url $url

    with-env {PG_DATABASE_URL: $url} {
        nu ($env.DEVENV_ROOT | path join scripts migrate.nu) up
    }
    render $filter 'schema="data_proxy"' | run-sql test $port

    let pushdown_sql = try {
        open --raw $pushdown
    } catch {|err| fail $'Could not read ($pushdown): ($err.msg)' {
            command: main
            span: (metadata $pushdown).span
        } }
    $pushdown_sql | run-sql test $port

    # PostgREST runs the pre-request function as the role of the token. The console roles exist above and anon exists now.
    render ($env.DEVENV_ROOT | path join helm files templates postgres grant_console_usage.sql) 'app_schema="data_proxy"' 'console_role="console_viewer"' | run-sql test $port
    'GRANT EXECUTE ON FUNCTION data_proxy.pre_request() TO anon' | run-sql test $port
}
