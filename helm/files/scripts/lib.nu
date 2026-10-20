use std/log

# Log an error and stop the script with the same message.
export def fail [message: string]: nothing -> error {
    log error $message
    error make --unspanned {msg: $message}
}

# Return the output of a finished command, or fail with its error text.
@example "A command that failed" {
    {stdout: '', stderr: 'boom', exit_code: 1} | check-result 'the command'
}
export def check-result [what: string]: record<stdout: string, stderr: string, exit_code: int> -> string {
    let result = $in

    if $result.exit_code != 0 {
        fail $'($what) failed: ($result.stderr | str trim)'
    }

    $result.stdout | str trim
}

# Run a check until it returns true, and fail when the timeout ends first.
@example "A check that is true at once" {
    wait-until 'the answer' {|| true }
}
export def wait-until [
    description: string
    check: closure
    --timeout: duration = 10min
    --interval: duration = 2sec
]: nothing -> nothing {
    let deadline = (date now) + $timeout

    loop {
        if (do $check) {
            log info $'Done waiting for ($description)'
            return
        }

        if (date now) >= $deadline {
            fail $'Timed out after ($timeout) waiting for ($description)'
        }

        log info $'Waiting for ($description)'
        sleep $interval
    }
}

# Dump one PostgreSQL table to a file using pg_dump.
@example "Dump one table" {
    dump-table postgres://user:password@host/db {
        schema: test
        table: access_policy
        file: /tmp/access_policy.dump
    }
}
export def dump-table [dsn: string, config: record<schema: string, table: string, file: string>]: nothing -> nothing {
    try {
        pg_dump $dsn --format=custom --no-owner --no-acl --enable-row-security --table=$"($config.schema).($config.table)" --data-only --file=($config.file)
    } catch {|err| fail $"pg_dump failed for ($config.schema).($config.table): ($err.msg)" }
}

# Return the list of schemas to process, filtered by SCHEMA env var when set.
@example "Return all configured schemas" {
    {
        schemas: {
            alpha: {}
            beta: {}
        }
    }
    | schema-list
} --result "[alpha, beta]"
export def schema-list []: record -> list<string> {
    let all = $in.schemas | columns
    let target = $env.SCHEMA?

    if $target == null or ($target | is-empty) {
        return $all
    }

    $all | where $it == $target
}

# Run one read query on the database and return the rows as text, one line for each row and a comma between the columns.
@example "Read the names of the schemas" {
    query-sql 'SELECT name FROM console.schemas' | lines
}
export def query-sql [query: string]: nothing -> string {
    psql $env.PG_DATABASE_URL --no-psqlrc --quiet -t -A -F ',' -c $query | complete | check-result 'SQL query'
}

# Read the synchronization configuration from the console tables or fail with contextual logging.
@example "Read configured schemas" {
    sync-config | get schemas | columns
}
export def sync-config []: nothing -> record {
    query-sql 'SELECT console.sync_config()' | from json
}

# Wait until a relation exists in PostgreSQL or fail after the configured timeout.
@example "Wait for the state table" {
    wait-for-relation data_proxy.state --timeout 1min
}
export def wait-for-relation [
    relation: string
    --timeout: duration = 10min
    --interval: duration = 2sec
]: nothing -> nothing {
    let query = 'SELECT to_regclass(' + (quote-literal $relation) + ') IS NOT NULL'

    wait-until $'relation ($relation)' --timeout $timeout --interval $interval {||
        let result = psql --no-psqlrc --quiet -t -A -c $query | complete

        $result.exit_code == 0 and ($result.stdout | str trim) == 't'
    }
}

# Return the psql arguments for one PostgreSQL readiness probe.
@example "Include a connection DSN" {
    postgres-ready-args postgres://test
} --result "[postgres://test, --no-psqlrc, --quiet, -t, -A, -c, SELECT 1]"
export def postgres-ready-args [dsn?: string]: nothing -> list<string> {
    let args = [
        --no-psqlrc
        --quiet
        -t
        -A
        -c
        'SELECT 1'
    ]

    if $dsn == null {
        return $args
    }

    [$dsn ...$args]
}

# Wait until PostgreSQL accepts connections or fail after the configured timeout.
@example "Probe with a test closure" {
    wait-for-postgres postgres://test --timeout 0sec --probe {|args|
        {exit_code: 0 stderr: ''}
    }
}
export def wait-for-postgres [
    dsn?: string
    --timeout: duration = 10min
    --interval: duration = 2sec
    --probe: closure
]: nothing -> nothing {
    let args = postgres-ready-args $dsn

    wait-until PostgreSQL --timeout $timeout --interval $interval {||
        let result = if $probe == null { psql ...$args | complete } else { do $probe $args }

        $result.exit_code == 0
    }
}

# Quote a PostgreSQL identifier for use in rendered SQL.
@example "Quote an identifier that holds a double quote" {
    quote-identifier 'test"schema'
} --result '"test""schema"'
export def quote-identifier [value: string]: nothing -> string {
    let escaped = $value | str replace --all '"' '""'

    $'"($escaped)"'
}

# Quote a PostgreSQL literal for use in rendered SQL.
@example "Quote a Portuguese literal" {
    quote-literal "d'água"
} --result "'d''água'"
export def quote-literal [value: string]: nothing -> string {
    let escaped = $value | str replace --all "'" "''"

    $"'($escaped)'"
}

# Return the quoted identifier of the application schema, from DBOS_APP_SCHEMA or the default.
export def app-schema []: nothing -> string {
    quote-identifier ($env.DBOS_APP_SCHEMA? | default data_proxy)
}

# Return the names of the roles as quoted literals, separated by commas, for a SQL list.
@example "Quote two role names" {
    [dp_api dp_admin] | role-literals
} --result "'dp_api', 'dp_admin'"
export def role-literals []: list<string> -> string {
    each {|name| quote-literal $name } | str join ', '
}

# Read the rbac.toml file and return what the create_rbac template needs: the parsed rules and the hash of the file text.
export def rbac-context [file?: path]: nothing -> record {
    let file = $file | default ($env.RBAC_FILE? | default ./rbac.toml)

    if not ($file | path exists) {
        fail $'The rules file does not exist: ($file)'
    }

    let text = open --raw $file | decode utf-8

    {
        rules: ($text | from toml)
        source_hash: ($text | hash sha256)
    }
}

# Render one Jinja SQL template with a strict JSON context.
@example "Render one SQL template" {
    render-sql postgres/grant_app_usage.sql {
        app_schema: data_proxy
        anonymous_role: anon
        user_role: dp_api
    }
}
export def render-sql [name: path, context: record]: nothing -> string {
    let context_file = mktemp --tmpdir --suffix .json
    let template_dir = $env.SQL_TEMPLATE_DIR? | default /templates

    $context | to json | save --force $context_file

    let result = minijinja-cli --strict --autoescape none --format json $'($template_dir)/($name)' $context_file | complete
    rm --force $context_file

    $result | check-result $'rendering of the SQL template ($name)'
}

# Render a Jinja SQL template and execute it against PostgreSQL.
@example "Execute SQL with template context" {
    execute-sql postgres/grant_app_usage.sql {
        app_schema: data_proxy
        anonymous_role: anon
        user_role: dp_api
    }
}
export def execute-sql [
    template: path
    context: record = {}
    --dsn: string
    --vars: record = {}
    --single-transaction # Run the whole template in one transaction, so that an error changes nothing
]: nothing -> nothing {
    let query = render-sql $template $context
    let dsn = $dsn | default {|| $env.PG_DATABASE_URL }
    let var_args = if ($vars | is-empty) { [] } else {
        $vars | items {|k v| [--set $"($k)=($v)"] } | flatten
    }
    let transaction_args = if $single_transaction { [--single-transaction] } else { [] }

    $query
    | psql $dsn --no-psqlrc --quiet -v ON_ERROR_STOP=1 ...$transaction_args ...$var_args
    | complete
    | check-result $'SQL execution of ($template)'
    | ignore
}

# Return the configured object-store endpoint with an explicit scheme.
export def endpoint-url []: nothing -> string {
    let endpoint = $env.S3_ENDPOINT

    if ($endpoint | str starts-with 'http') {
        return $endpoint
    }

    let scheme = if ($env.S3_USE_SSL? | default 'false') == 'true' { 'https' } else { 'http' }

    $'($scheme)://($endpoint)'
}

# Return the rclone remote environment for the configured object store. The credentials default to AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.
export def rclone-env [
    --access-key: string
    --secret-key: string
]: nothing -> record {
    let endpoint = endpoint-url

    if ($endpoint | str contains 'googleapis.com') {
        return {
            RCLONE_CONFIG_STORE_TYPE: 'google cloud storage'
            RCLONE_CONFIG_STORE_SERVICE_ACCOUNT_FILE: $env.GOOGLE_APPLICATION_CREDENTIALS
            RCLONE_CONFIG_STORE_BUCKET_POLICY_ONLY: 'true'
        }
    }

    {
        RCLONE_CONFIG_STORE_TYPE: 's3'
        RCLONE_CONFIG_STORE_PROVIDER: 'Other'
        RCLONE_CONFIG_STORE_ENDPOINT: $endpoint
        RCLONE_CONFIG_STORE_ACCESS_KEY_ID: ($access_key | default {|| $env.AWS_ACCESS_KEY_ID })
        RCLONE_CONFIG_STORE_SECRET_ACCESS_KEY: ($secret_key | default {|| $env.AWS_SECRET_ACCESS_KEY })
        RCLONE_CONFIG_STORE_FORCE_PATH_STYLE: 'true'
        RCLONE_CONFIG_STORE_REGION: 'auto'
        RCLONE_CONFIG_STORE_NO_CHECK_BUCKET: 'true'
    }
}

# The tables that every schema of the data has, and that no other schema has.
const MARKER_TABLES = [access_policy access_log]

# The schemas of the system, which a removal never touches.
const SYSTEM_SCHEMAS = [console data_proxy dbos rbac public]

# Find the schemas that the console no longer lists: the schemas with both marker tables, except the system.
@example "A schema with both tables and no row in the console" {
    [[schema table]; [e2e_new access_policy] [e2e_new access_log] [test access_policy] [test access_log]]
    | orphans [test]
} --result [e2e_new]
export def orphans [configured: list<string>]: table<schema: string, table: string> -> list<string> {
    group-by schema
    | transpose schema rows
    | where ($MARKER_TABLES | all {|marker| $marker in ($it.rows | get table) })
    | get schema
    | where $it not-in $SYSTEM_SCHEMAS and $it not-in $configured
    | sort
}

# The remote path of the data files of one schema. It refuses a name that could leave the catalog folder.
@example "The folder of one schema" {
    data-prefix bucket ducklake e2e_new
} --result 'store:bucket/ducklake/e2e_new'
export def data-prefix [bucket: string, catalog_path: string, schema: string]: nothing -> string {
    if ($schema | is-empty) or $schema =~ '/|\.\.' {
        fail $'Refusing to purge the data of the schema "($schema)"'
    }

    $'store:($bucket)/($catalog_path)/($schema)'
}
