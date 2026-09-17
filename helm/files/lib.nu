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
        _ => { error make {
            msg: $'Unknown PostgreSQL quote kind: ($kind)'
            label: {
                text: quote-kind
                span: (metadata $kind).span
            }
        } }
    }
}

# Render one Jinja SQL template with a strict JSON context.
export def render-sql [name: string, context: record]: nothing -> string {
    let context_file = '/tmp/context.json'

    try {
        $context | to json | save --force $context_file
        let template_dir = $env.SQL_TEMPLATE_DIR? | default /sql
        minijinja-cli --strict --autoescape none --format json $'($template_dir)/($name)' $context_file
    } catch {|err| error make {
        msg: $'Failed to render SQL template ($name): ($err.msg)'
        label: {
            text: render-sql
            span: (metadata $name).span
        }
    } }
}
