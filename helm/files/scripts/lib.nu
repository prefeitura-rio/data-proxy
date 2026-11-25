use std/log

# Log an error and stop the script with contextual failure details.
@example "Stop with an error" { fail 'Example failure' }
export def fail [message: string]: nothing -> error {
    log error $message

    error make {
        msg: $message
        labels: [
            {text: 'raised here', span: (metadata $message).span}
        ]
    }
}

# Return the output of a finished command, or fail with its error text.
@example "Read successful command output" { {stdout: 'ready', stderr: '', exit_code: 0} | check-result 'probe' } --result 'ready'
export def check-result [what: string]: record<stdout: string, stderr: string, exit_code: int> -> string {
    let result = $in

    if $result.exit_code != 0 {
        fail $'($what) failed: ($result.stderr | str trim)'
    }

    $result.stdout | str trim
}

# Run a check until it returns true, and fail when the timeout ends first.
@example "Wait for a ready check" { wait-until 'readiness' {|| true } }
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
@example "Dump an access table" { dump-table 'postgresql://localhost/example' {schema: access, table: policy, file: '/tmp/policy.dump'} }
export def dump-table [dsn: string, config: record<schema: string, table: string, file: string>]: nothing -> nothing {
    try {
        pg_dump $dsn --format=custom --no-owner --no-acl --enable-row-security --table=$"($config.schema).($config.table)" --data-only --file=($config.file)
    } catch {|err| fail $"pg_dump failed for ($config.schema).($config.table): ($err.msg)" }
}

# Return the list of schemas to process, filtered by SCHEMA env var when set.
@example "Read configured schemas" { with-env {SCHEMA: ''} { {schemas: {alpha: {}, beta: {}}} | schema-list } } --result [alpha beta]
export def schema-list []: record -> list<string> {
    let all = $in.schemas | columns
    let target = $env.SCHEMA?

    if $target == null or ($target | is-empty) {
        return $all
    }

    $all | where $it == $target
}

# Run one read query on the database and return the rows as text, one line for each row and a comma between the columns.
@example "Read configured schema names" { query-sql 'SELECT name FROM console.schemas' }
export def query-sql [query: string]: nothing -> string {
    psql $env.PG_DATABASE_URL --no-psqlrc --quiet -t -A -F ',' -c $query | complete | check-result 'SQL query'
}

# Read the synchronization configuration from the console tables or fail with contextual logging.
@example "Read configured schema names" { sync-config | get schemas | columns }
export def sync-config []: nothing -> record {
    query-sql 'SELECT console.sync_config()' | from json
}

# Wait until a relation exists in PostgreSQL or fail after the configured timeout.
@example "Wait for a state table" { wait-for-relation 'data_proxy.state' }
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

# Wait until the configured SQL probe succeeds or reaches its timeout.
@example "Wait for database roles" { wait-for-sql 'request roles' "SELECT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon')" }
export def wait-for-sql [
    description: string
    probe: string
    --dsn: string
    --timeout: duration = 10min
    --interval: duration = 2sec
]: nothing -> nothing {
    let args = [
        ...(if $dsn == null { [] } else { [$dsn] })
        --no-psqlrc
        --quiet
        -t
        -A
        -c
        $probe
    ]

    wait-until $description --timeout $timeout --interval $interval {||
        let result = psql ...$args | complete

        $result.exit_code == 0 and ($result.stdout | str trim) == 't'
    }
}

# Return the psql arguments for one PostgreSQL readiness probe.
@example "Construct probe arguments" { postgres-ready-args 'postgresql://localhost/example' }
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
@example "Wait for PostgreSQL" { wait-for-postgres 'postgresql://localhost/example' }
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
@example "Quote an identifier" { quote-identifier 'orders' } --result '"orders"'
export def quote-identifier [value: string]: nothing -> string {
    let escaped = $value | str replace --all '"' '""'

    $'"($escaped)"'
}

# Quote a PostgreSQL literal for use in rendered SQL.
@example "Quote a SQL literal" { quote-literal 'orders' } --result "'orders'"
export def quote-literal [value: string]: nothing -> string {
    let escaped = $value | str replace --all "'" "''"

    $"'($escaped)'"
}

# Return the quoted identifier of the application schema, from DBOS_APP_SCHEMA or the default.
@example "Quote the application schema" { with-env {DBOS_APP_SCHEMA: data_proxy} { app-schema } } --result '"data_proxy"'
export def app-schema []: nothing -> string {
    quote-identifier ($env.DBOS_APP_SCHEMA? | default data_proxy)
}

# Return the names of the roles as quoted literals, separated by commas, for a SQL list.
@example "Quote request roles" { [dp_api dp_admin] | role-literals } --result "'dp_api', 'dp_admin'"
export def role-literals []: list<string> -> string {
    each {|name| quote-literal $name } | str join ', '
}

# Read the rbac.toml file and return what the load_rbac template needs: the parsed rules and the hash of the file text.
@example "Read RBAC template inputs" { rbac-context 'helm/files/config/rbac.toml' | get source_hash }
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
@example "Render a role template" { render-sql 'postgres/reset_db_schemas.sql' {serving_role: '"serving"'} }
export def render-sql [name: path, context: record]: nothing -> string {
    let context_file = mktemp --tmpdir --suffix .json
    let template_dir = $env.SQL_TEMPLATE_DIR? | default /templates

    $context | to json | save --force $context_file

    let result = minijinja-cli --strict --autoescape none --format json $'($template_dir)/($name)' $context_file | complete
    rm --recursive --force $context_file

    $result | check-result $'rendering of the SQL template ($name)'
}

# Render a Jinja SQL template and execute it against PostgreSQL.
@example "Apply a role template" { execute-sql 'postgres/reset_db_schemas.sql' {serving_role: '"serving"'} --dsn 'postgresql://localhost/example' }
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
@example "Normalize an object-store endpoint" { with-env {S3_ENDPOINT: 'localhost:8333', S3_USE_SSL: 'false'} { endpoint-url } } --result 'http://localhost:8333'
export def endpoint-url []: nothing -> string {
    let endpoint = $env.S3_ENDPOINT

    if ($endpoint | str starts-with 'http') {
        return $endpoint
    }

    let scheme = if ($env.S3_USE_SSL? | default 'false') == 'true' { 'https' } else { 'http' }

    $'($scheme)://($endpoint)'
}

# Return the configured object-store environment with AWS credential defaults.
@example "Inspect an object-store backend" { with-env {S3_ENDPOINT: 'localhost:8333'} { rclone-env --access-key example --secret-key example | get RCLONE_CONFIG_STORE_TYPE } } --result 's3'
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

# The schemas of the system, which a removal never touches.
const SYSTEM_SCHEMAS = [console data_proxy dbos rbac public access]

# Return data schemas that the console no longer configures.
@example "Find an obsolete data schema" { [public default__old sales__orders] | orphans sales__orders } --result [default__old]
export def orphans [...configured: string]: list<string> -> list<string> {
    where $it =~ '^[a-z][a-z0-9]*__'
    | where $it not-in $SYSTEM_SCHEMAS and $it not-in $configured
    | sort
}

# Return the legacy S3 folder name for a schema.
@example "Read a legacy storage name" { legacy-name sales__orders } --result 'sales_orders'
export def legacy-name [schema: string]: nothing -> string {
    let parts = $schema | split row '__' --number 2

    match $parts {
        [_] => $schema
        ['default' $short] => $short
        [$project $short] => $'($project)_($short)'
    }
}

# Return a schema data path while refusing names outside the catalog folder.
@example "Locate schema data files" { data-prefix bucket ducklake sales__orders } --result 'store:bucket/ducklake/sales_orders'
export def data-prefix [bucket: string, catalog_path: string, schema: string]: nothing -> string {
    if ($schema | is-empty) or $schema =~ '/|\.\.' {
        fail $'Refusing to purge the data of the schema "($schema)"'
    }

    $'store:($bucket)/($catalog_path)/(legacy-name $schema)'
}

# The fields that a JWKS key of each type needs, next to its kid.
const KEY_FIELDS = {
    RSA: [n e]
    EC: [crv x y]
    OKP: [crv x]
}

# Return whether a JWKS key has a kid, a known type, and every field of that type.
@example "Validate an RSA key shape" { {kid: example, kty: RSA, n: modulus, e: AQAB} | jwks-key-valid } --result true
export def jwks-key-valid []: record -> bool {
    let key = $in
    let fields = if $key.kty? == null { null } else { $KEY_FIELDS | get --optional $key.kty }

    $fields != null and ([kid ...$fields] | all {|field| ($key | get --optional $field) not-in [null ''] })
}

# Return the identifiers of running sync pods that have no deletion timestamp.
@example "Read live worker identifiers" { {items: [{metadata: {uid: worker}, status: {phase: Running}}]} | live-executor-ids } --result [worker]
export def live-executor-ids []: record -> list<string> {
    get items
    | where $it.status.phase == Running
    | where $it.metadata.deletionTimestamp? == null
    | get metadata.uid
}

# Return the access tables and dated storage folder for a backup.
@example "Locate a daily backup" { backup-plan bucket backups '2026-01-01' | get remote_prefix } --result 'store:bucket/backups/2026-01-01'
export def backup-plan [bucket: string, prefix: string, date: string]: nothing -> record {
    {
        dumps: [
            {table: policy, file: '/tmp/access-policy.dump'}
            {table: log, file: '/tmp/access-log.dump'}
        ]
        remote_prefix: $'store:($bucket)/($prefix)/($date)'
    }
}

# Return the PostgREST schema list with data schemas before console and access.
@example "Order served schemas" { served-schemas sales__orders default__people } --result 'default__people,sales__orders,console,access'
export def served-schemas [...names: string]: nothing -> string {
    ($names | sort) ++ ['console' 'access'] | str join ','
}
