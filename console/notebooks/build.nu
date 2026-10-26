#!/usr/bin/env nu

# The version of the Parquet extension that matches DuckDB-WASM 1.29.0
const EXTENSION_VERSION = 'v1.1.1'

# Log an error and stop the script with the same message. The label points at the message in the caller.
def fail [message: string]: nothing -> error {
    error make {
        msg: $message
        labels: [
            {text: 'raised here', span: (metadata $message).span}
        ]
    }
}

# Build the notebook site: export a marimo notebook that runs offline, bundle the page script and the worker script
# with DuckDB-WASM, and add the files that DuckDB-WASM needs.
def main [
    notebook: path = notebooks/query.py # The marimo notebook to export
    output: path = dist # The folder for the site
    --mode: string = edit # edit lets the user write cells, run makes a read-only page
]: nothing -> nothing {
    try {
        cd $env.FILE_PWD
    } catch {|err|
        fail $'Cannot enter ($env.FILE_PWD): ($err.msg)'
    }

    # The devenv shell points uv at the environment of sync/. This project keeps its own.
    $env.UV_PROJECT_ENVIRONMENT = ($env.FILE_PWD | path join .venv)

    pnpm install --frozen-lockfile
    uv sync --frozen

    try {
        rm --recursive --force $output
    } catch {|err|
        fail $'Cannot remove ($output): ($err.msg)'
    }

    # --no-sandbox: marimo asks on a terminal whether to run in a sandbox, and the task waits for an answer.
    # The project has its own environment, which uv sync made above.
    uv run --frozen marimo export html-wasm $notebook --output $output --mode $mode --offline --force --no-sandbox

    let duckdb = [$output duckdb] | path join
    let extension_folder = [$duckdb ext $EXTENSION_VERSION wasm_eh] | path join

    try {
        mkdir $extension_folder
    } catch {|err|
        fail $'Cannot create ($extension_folder): ($err.msg)'
    }

    node bundle.ts $output

    for file in [duckdb-browser-eh.worker.js duckdb-eh.wasm] {
        try {
            cp ([node_modules @duckdb duckdb-wasm dist $file] | path join) $duckdb
        } catch {|err|
            fail $'Cannot copy ($file): ($err.msg)'
        }
    }

    let extension = 'parquet.duckdb_extension.wasm'
    try {
        http get $'https://extensions.duckdb.org/($EXTENSION_VERSION)/wasm_eh/($extension)'
        | save --force ([$extension_folder $extension] | path join)
    } catch {|err|
        fail $'Cannot download ($extension): ($err.msg)'
    }

    let index = [$output index.html] | path join
    try {
        open --raw $index
        | str replace '</head>' '<script src="/config.js"></script><script src="/bridge.js"></script></head>'
        | save --force $index
    } catch {|err|
        fail $'Cannot update ($index): ($err.msg)'
    }
}
