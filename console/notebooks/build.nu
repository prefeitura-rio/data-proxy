#!/usr/bin/env nu

# Build the notebook site into one folder: `nu console/notebooks/build.nu notebooks/query.py dist`. The script installs the
# locked dependencies, exports the notebook with `marimo export html-wasm --offline`, bundles the two TypeScript scripts
# with DuckDB-WASM (bundle.ts, esbuild), downloads the Parquet extension for the pinned DuckDB version, and adds the
# scripts and the stylesheet that hides the SQL cell button to index.html. The pins are marimo and Playwright in pyproject.toml, and DuckDB-WASM in package.json. The export
# needs a Chromium, so Containerfile.console builds the site in a first stage that has one. A notebook fails to run when
# two cells define the same name, so give each variable a name that no other cell uses.

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
# with DuckDB-WASM, and add the files that DuckDB-WASM needs. The devenv shell points uv at the environment of sync/,
# so the script sets the environment of this project. marimo asks on a terminal whether to run in a sandbox, so the
# export passes --no-sandbox and uses the environment that uv sync made.
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

    $env.UV_PROJECT_ENVIRONMENT = ($env.FILE_PWD | path join .venv)

    pnpm install --frozen-lockfile
    uv sync --frozen

    try {
        rm --recursive --force $output
    } catch {|err|
        fail $'Cannot remove ($output): ($err.msg)'
    }

    uv run --frozen marimo export html-wasm $notebook --output $output --mode $mode --offline --force --no-sandbox

    let duckdb = [$output duckdb] | path join
    let extension_folder = [$duckdb ext $EXTENSION_VERSION wasm_eh] | path join

    try {
        mkdir $extension_folder
    } catch {|err|
        fail $'Cannot create ($extension_folder): ($err.msg)'
    }

    node bundle.ts $output

    try {
        cp src/hide.css $output
    } catch {|err|
        fail $'Cannot copy hide.css: ($err.msg)'
    }

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
        | str replace '</head>' '<link rel="stylesheet" href="/hide.css"><script src="/config.js"></script><script src="/bridge.js"></script></head>'
        | save --force $index
    } catch {|err|
        fail $'Cannot update ($index): ($err.msg)'
    }
}
