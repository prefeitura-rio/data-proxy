use lib.nu [
    check-result
    fail
    parse-toml
    read-text
    remove-path
    write-text
]

const MODEL = 'docs/model.md'
const DIAGRAMS = 'docs/diagrams'
const TBLS_CONFIGS = ['docs/.tbls.yaml' 'docs/.tbls.catalog.yaml']

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
# The policies and the functions of every data schema ask rbac.can(), so the data tests need the rules too.
# DuckDB reads persistent secrets once per backend, so the setup creates them before any test backend starts.
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

    let rules_text = read-text ($env.DEVENV_ROOT | path join helm files config rbac.toml)
    let rbac_sql = render ($env.DEVENV_ROOT | path join helm files templates postgres create_rbac.sql) {
        rules: ($rules_text | parse-toml)
        anonymous_role: '"anon"'
        source_hash: ($rules_text | hash sha256)
    }
    for database in ['test_template' 'test'] {
        $rbac_sql | run-sql $database $port
    }

    render ($templates | path join create_ducklake_catalog_secret.sql) {
        host: localhost
        port: $port
        database: ducklake
        user: test
        password: test
    } | run-sql test $port

    'CREATE EXTENSION IF NOT EXISTS pg_duckdb;' | run-sql test $port
}

# Replace the text between the markers of a block in a page, such as `diagram:application`.
export def embed-block [page: string, marker: string, text: string]: nothing -> string {
    let start = $'<!-- ($marker):start -->'
    let end = $'<!-- ($marker):end -->'
    let from = $page | str index-of $start
    let to = $page | str index-of $end

    if $from < 0 or $to < $from {
        fail $'The page has no block named ($marker)'
    }

    [
        ($page | str substring 0..<($from + ($start | str length)))
        ($text | str trim)
        ($page | str substring $to..)
    ]
    | str join (char nl)
}

# Replace the diagram between the markers of one name in a page with a `mermaid` fence.
export def embed-diagram [
    page: string
    name: string
    title: string
    diagram: string
]: nothing -> string {
    embed-block $page $'diagram:($name)' (
        [
            '```mermaid'
            $'%% ($title)'
            ($diagram | str trim)
            '```'
        ]
        | str join (char nl)
    )
}

# Join the diagrams into one `erDiagram`: one header, then the lines of every part.
export def join-diagrams [...diagrams: string]: nothing -> string {
    $diagrams
    | each {|diagram| $diagram | lines | skip 1 | where $it != '' }
    | flatten
    | prepend erDiagram
    | str join (char nl)
}

# Return one row for each base table that no diagram draws, with its comment and its group (the first label).
export def other-tables [tables: list<record>, ...shown: string]: nothing -> list<record<Group: string, Table: string, Contents: string>> {
    $tables
    | where type == 'BASE TABLE' and name not-in $shown
    | each {|table|
        {
            Group: ($table | get --optional labels | default [] | get name | append '-' | first)
            Table: $'`($table.name | split row '.' | last)`'
            Contents: (if ($table.comment | is-empty) { '-' } else { $table.comment })
        }
    }
    | sort-by Group Table
}

# Run tbls and return its output. tbls writes its errors to stdout, so the failure shows both streams.
def run-tbls [config: path, format: string, viewpoint: string = '', out: string = '']: nothing -> string {
    let flags = [
        --format $format
        ...(if $viewpoint == '' { [] } else { [--viewpoint $viewpoint] })
        ...(if $out == '' { [] } else { [--out $out] })
    ]

    let result = tbls out --config $config ...$flags | complete

    if $result.exit_code != 0 {
        let reason = [$result.stdout $result.stderr] | each { str trim } | where $it != '' | str join ' '

        fail $'tbls ($config) failed: ($reason). Run `devenv --profile repo tasks run docs:build:model`, which starts the databases and stops them.'
    }

    $result.stdout
}

# Return the tables of a database as tbls reads them, or only the tables of one viewpoint.
def tbls-tables [config: path, viewpoint: string = '']: nothing -> record {
    let output = run-tbls $config json $viewpoint

    try {
        $output | from json
    } catch {|err|
        fail $'Cannot read the output of tbls ($config): ($err.msg)'
    }
}

# Write the diagrams of the viewpoints of one config to docs/diagrams/ and return them, with the other tables.
def config-diagrams [config: path]: nothing -> record {
    let folder = $env.DEVENV_ROOT | path join $DIAGRAMS
    let doc = tbls-tables $config

    let diagrams = $doc.viewpoints
    | enumerate
    | each {|entry|
        let viewpoint = $entry.index | into string
        let file = $folder | path join $'($entry.item.name).mmd'

        run-tbls $config mermaid $viewpoint $file | ignore

        {
            name: $entry.item.name
            title: $entry.item.desc
            text: (read-text $file)
            tables: (tbls-tables $config $viewpoint | get tables.name)
        }
    }

    {
        diagrams: $diagrams
        others: (other-tables $doc.tables ...($diagrams | get tables | flatten))
    }
}

# Write the diagrams of the tables to docs/diagrams/ and put them in docs/model.md.
# Run it with `devenv --profile repo tasks run docs:build:model`, which starts the databases first.
def "main diagrams" []: nothing -> nothing {
    let results = $TBLS_CONFIGS | each {|config| config-diagrams ($env.DEVENV_ROOT | path join $config) }
    let diagrams = $results | get diagrams | flatten

    let all = {
        name: all-tables
        title: 'All database tables'
        text: (join-diagrams ...($diagrams | get text))
    }
    $all.text | write-text ($env.DEVENV_ROOT | path join $DIAGRAMS all-tables.mmd)

    let page = $env.DEVENV_ROOT | path join $MODEL

    $diagrams
    | prepend $all
    | reduce --fold (read-text $page) {|diagram, text| embed-diagram $text $diagram.name $diagram.title $diagram.text }
    | embed-block $in tables:other ($results | get others | flatten | to md --pretty)
    | write-text $page
}
