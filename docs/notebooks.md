# Notebooks

The query tab of the console embeds a notebook that runs in the browser. `console/notebooks/` builds the static site of that notebook. The site runs on its own origin, so it shares no cookie with the console.

## What the site contains

The site is a static folder. It contains a marimo notebook, DuckDB compiled to WebAssembly, and the files that the notebook loads.

- A marimo notebook, exported with `marimo export html-wasm --offline`. Python runs in Pyodide. The export contains Pyodide and its packages, so the page loads nothing from the internet.
  - The notebook is in edit mode, so the user can write cells and download the notebook as a `.py` file.
  - Edit mode doesn't run the cells at load, so the user presses Run once.
  - To download the notebook, the user presses Ctrl+S first, and then chooses Export, Python. Without the save, marimo exports an empty file.
  - The file contains the cells and no link, because a grant never enters the code of the notebook.
- DuckDB compiled to WebAssembly, bundled with `apache-arrow` and served from the same site. The Parquet extension is on the site too, in `duckdb/ext/<version>/wasm_eh/`.
  - DuckDB-WASM loads the extension on the first read of a Parquet file.
  - The policy of the site blocks the public repository, so the helper sets `custom_extension_repository` to this site.
- `src/helper.ts`, which starts DuckDB-WASM in the Python worker and applies each grant. For each table, it registers the signed links and creates a view. A data file with a delete file becomes an anti join on the row number, so a count matches the database. A new grant replaces the registered files, so a notebook that stays open keeps working after the links of the first grant expire.
- `src/bridge.ts`, which runs in the page. It accepts a grant only from the origin of the console and only from the parent window, and it passes the grant to the worker. Both scripts check a grant with the valibot schema of `src/messages.ts`, which also contains the type of each status message. The worker and the page tell the console what happens (`ready`, `applied`, `error` and a violation of the policy).

The Python helper is in the first cells of `console/notebooks/notebooks/query.py`. `q(sql, limit=100_000)` runs SQL in DuckDB-WASM and returns at most `limit` rows as a DataFrame, because the Python heap is the weak point of the page. A scan of a large table stays in DuckDB-WASM. When the links expire, `q` raises an error that tells the user to open the notebook again.

## Build

`console/notebooks/build.nu` builds the notebook site into one folder. This command is the usual run.

```bash
nu console/notebooks/build.nu notebooks/query.py dist
```

The script does these things:

- It installs the locked dependencies and exports the notebook.
- It bundles the two TypeScript scripts with DuckDB-WASM (`bundle.ts`, esbuild).
- It downloads the Parquet extension for the pinned DuckDB version.
- It adds the two scripts to `index.html`.

The build pins the versions: marimo and Playwright in `pyproject.toml`, DuckDB-WASM in `package.json`. The export needs a Chromium. `Containerfile.console` builds the site in a first stage that has one, and copies it into the image of the console.

The chart starts that image a second time with `node notebooks/server.ts`, as its own Deployment on its own hostname. Node 24 runs the TypeScript file directly, so the server needs no build step. `erasableSyntaxOnly` in `tsconfig.json` keeps the code to the syntax that Node can strip.

A marimo notebook fails to run when two cells define the same name, and the page stays blank. Give each variable of a cell a name that no other cell uses.

## Serve

The server sends this Content Security Policy:

```text
default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline' 'wasm-unsafe-eval' blob:; worker-src 'self' blob:; connect-src 'self' blob: data: <storage origin>; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; frame-ancestors <console origin>
```

`console/notebooks/csp.ts` builds it for the browser test, and the chart builds the same string for the Deployment. `console/notebooks/server.ts` sends it from the setting `CSP`. The server also answers `/config.js` with the origin of the console, which `bridge.ts` reads. The page needs `unsafe-eval` and `unsafe-inline` because Pyodide and marimo use them. The policy limits where the page can send data: only to itself and to the storage host.

## How a test checks it

The test in `console/notebooks/tests/` builds a notebook that runs checks. It serves the notebook with the policy, and it loads the notebook in an `iframe` of a stub console on another origin.

The stub sends a grant for a table with 3000 rows and a delete file for 300 of them. The test passes when all these points are true:

- The policy blocks no request.
- The count is 2700.
- The page ignores a grant that it posts to itself.
- Python gets 100000 rows by default and 150000 when the call asks for them.

<!-- nav:start -->
---

[Previous: Glossary](glossary.md) | [Next: SQL Templates](sql.md) | [Home](index.md)
<!-- nav:end -->
