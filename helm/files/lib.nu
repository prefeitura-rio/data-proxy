use std/log

# Log an error and raise a labeled error in one call.
export def fail [message: string, context: record<command: string, span: record>]: nothing -> error {
    log error $message
    error make {
        msg: $message
        label: {text: $context.command, span: $context.span}
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
    } catch {|err| fail $"pg_dump failed for ($config.schema).($config.table): ($err.msg)" {
        command: dump-table
        span: (metadata $config.table).span
    } }
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

# Read the configured synchronization JSON or fail with contextual logging.
@example "Read configured schemas" {
    sync-config | get schemas | columns
}
export def sync-config []: nothing -> record {
    try {
        open $env.SYNC_CONFIG_PATH
    } catch {|err| fail $'Failed to open sync config path=($env.SYNC_CONFIG_PATH) error=($err.msg)' {
        command: sync-config
        span: (metadata $env.SYNC_CONFIG_PATH).span
    } }
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
    let deadline = (date now) + $timeout
    let args = postgres-ready-args $dsn

    loop {
        let result = if $probe == null {
            psql ...$args | complete
        } else {
            do $probe $args
        }

        if $result.exit_code == 0 {
            log info 'PostgreSQL is ready'
            return
        }

        if (date now) >= $deadline {
            fail $'Timed out after ($timeout) waiting for PostgreSQL: ($result.stderr | str trim)' {
                command: wait-for-postgres
                span: (metadata $timeout).span
            }
        }

        log info 'Waiting for PostgreSQL...'
        sleep $interval
    }
}

# Quote a PostgreSQL value for use in rendered SQL.
@example "Quote an identifier and a Portuguese literal" {
    [
        (quote-pg 'test"schema' identifier)
        (quote-pg "d'água" literal)
    ]
} --result "[\"test\"\"schema\", 'd''água']"
export def quote-pg [value: string, kind: string]: nothing -> string {
    match $kind {
        identifier => {
            let escaped = $value | str replace --all '"' '""'
            $'"($escaped)"'
        }
        literal => {
            let escaped = $value | str replace --all "'" "''"
            $"'($escaped)'"
        }
        _ => {
            fail $'Unknown PostgreSQL quote kind: ($kind)' {
                command: quote-pg
                span: (metadata $kind).span
            }
        }
    }
}

# Render one Jinja SQL template with a strict JSON context.
@example "Render one SQL template" {
    render-sql postgres/grant_rls_usage.sql {
        anonymous_role: anon
        user_role: user
    }
}
export def render-sql [name: path, context: record]: nothing -> string {
    let context_file = '/tmp/context.json'

    try {
        $context | to json | save --force $context_file
        let template_dir = $env.SQL_TEMPLATE_DIR? | default /templates
        minijinja-cli --strict --autoescape none --format json $'($template_dir)/($name)' $context_file
    } catch {|err| fail $'Failed to render SQL template ($name): ($err.msg)' {
        command: render-sql
        span: (metadata $name).span
    } }
}

# Render a Jinja SQL template and execute it against PostgreSQL.
@example "Execute SQL with template context" {
    execute-sql postgres/grant_rls_usage.sql {
        anonymous_role: anon
        user_role: user
    }
}
export def execute-sql [
    template: path
    context: record = {}
    --dsn: string
    --vars: record = {}
]: nothing -> nothing {
    let query = render-sql $template $context
    let dsn = $dsn | default $env.PG_DATABASE_URL
    let var_args = if ($vars | is-empty) { [] } else {
        $vars | items {|k v| [--set $"($k)=($v)"] } | flatten
    }

    let result = $query | psql $dsn --no-psqlrc --quiet -v ON_ERROR_STOP=1 ...$var_args | complete

    if $result.exit_code != 0 {
        fail $'SQL execution failed for ($template): ($result.stderr | str trim)' {
            command: execute-sql
            span: (metadata $template).span
        }
    }
}
