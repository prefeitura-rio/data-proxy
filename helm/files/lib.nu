use std/log

# Log an error and raise a labeled error in one call.
export def fail [message: string, context: record<command: string, span: record>]: nothing -> error {
    log error $message
    error make {
        msg: $message
        label: {
            text: $context.command
            span: $context.span
        }
    }
}

# Dump one PostgreSQL table to a file using pg_dump.
export def dump-table [dsn: string, config: record<schema: string, table: string, file: string>]: nothing -> nothing {
    try {
        pg_dump $dsn --format=custom --no-owner --no-acl --enable-row-security --table=$"($config.schema).($config.table)" --data-only --file=$config.file
    } catch {|err|
        fail $"pg_dump failed for ($config.schema).($config.table): ($err.msg)" {command: dump-table, span: (metadata $config.table).span}
    }
}

# Return the list of schemas to process, filtered by SCHEMA env var when set.
export def schema-list [config: record]: nothing -> list<string> {
    let all = $config.schemas | columns
    let target = $env.SCHEMA?

    if $target == null or ($target | is-empty) {
        $all
    } else {
        $all | where $it == $target
    }
}

# Quote a PostgreSQL value for use in rendered SQL.
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
        _ => { fail $'Unknown PostgreSQL quote kind: ($kind)' {command: quote-pg, span: (metadata $kind).span} }
    }
}

# Render one Jinja SQL template with a strict JSON context.
export def render-sql [name: string, context: record]: nothing -> string {
    let context_file = '/tmp/context.json'

    try {
        $context | to json | save --force $context_file
        let template_dir = $env.SQL_TEMPLATE_DIR? | default /templates
        minijinja-cli --strict --autoescape none --format json $'($template_dir)/($name)' $context_file
    } catch {|err| fail $'Failed to render SQL template ($name): ($err.msg)' {command: render-sql, span: (metadata $name).span} }
}

# Restart every PostgREST deployment so each instance reloads its schema cache.
# The read-only instance reads from a replica, where NOTIFY is never delivered.
export def refresh-postgrest [namespace: string, --context: string]: nothing -> nothing {
    for component in [postgrest-ro postgrest-rw] {
        let args = if ($context | is-empty) { [] } else { [--context=($context)] }

        let restarted = (kubectl ...$args --namespace $namespace rollout restart deployment --selector $"app.kubernetes.io/component=($component)" | complete)

        if $restarted.exit_code != 0 {
            log error $"PostgREST restart failed for ($component): ($restarted.stderr | str trim)"
        }
    }
}
