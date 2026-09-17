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
