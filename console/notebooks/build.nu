#!/usr/bin/env nu

# The version of the Parquet extension that matches DuckDB-WASM 1.29.0
const EXTENSION_VERSION = 'v1.1.1'

# Build the notebook site: export a marimo notebook that runs offline, bundle the page script and the worker script
# with DuckDB-WASM, and add the files that DuckDB-WASM needs.
def main [
    notebook: path = notebooks/query.py # The marimo notebook to export
    output: path = dist # The folder for the site
    --mode: string = edit # edit lets the user write cells, run makes a read-only page
]: nothing -> nothing {
    cd $env.FILE_PWD

    # The devenv shell points uv at the environment of sync/. This project keeps its own.
    $env.UV_PROJECT_ENVIRONMENT = ($env.FILE_PWD | path join .venv)

    pnpm install --frozen-lockfile
    uv sync --frozen --group test

    rm --recursive --force $output
    uv run --frozen marimo export html-wasm $notebook --output $output --mode $mode --offline --force

    let duckdb = [$output duckdb] | path join
    mkdir ([$duckdb ext $EXTENSION_VERSION wasm_eh] | path join)

    node bundle.ts $output

    for file in [duckdb-browser-eh.worker.js duckdb-eh.wasm] {
        cp ([node_modules @duckdb duckdb-wasm dist $file] | path join) $duckdb
    }

    let extension = 'parquet.duckdb_extension.wasm'
    http get $'https://extensions.duckdb.org/($EXTENSION_VERSION)/wasm_eh/($extension)'
    | save --force ([$duckdb ext $EXTENSION_VERSION wasm_eh $extension] | path join)

    let index = [$output index.html] | path join
    open --raw $index
    | str replace '</head>' '<script src="/config.js"></script><script src="/bridge.js"></script></head>'
    | save --force $index
}
