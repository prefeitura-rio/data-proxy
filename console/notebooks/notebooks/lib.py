"""Query the tables of the grant from a marimo notebook in the browser."""

import json

import polars as pl
from pyodide.code import run_js

# DuckDB-WASM starts when a notebook imports this file. query() waits for it.
STARTED = run_js(
    "import(self.location.origin + '/helper.js').then((module) => module.start())"
)

EXPIRED = "The links to the files expired. Open the notebook again from the Query tab."
NO_FILES = "The table has no data files yet. Sync it, then open the notebook again from the Query tab."


async def query(sql: str, limit: int = 100_000) -> pl.DataFrame:
    """Run SQL in DuckDB-WASM on the tables of the grant and return at most limit rows.

    The scan stays in DuckDB-WASM, so a large table never fills the Python heap.
    """
    await STARTED
    await run_js("globalThis.dataProxy.first")

    try:
        rows = json.loads(
            await run_js(
                f"globalThis.dataProxy.query({json.dumps(sql)}, {int(limit)})"
            )
        )
    except Exception as error:
        message = str(error)

        if "403" in message or "Forbidden" in message:
            raise RuntimeError(EXPIRED) from error

        if "does not exist" in message:
            raise RuntimeError(NO_FILES) from error

        raise

    return pl.DataFrame(rows, infer_schema_length=None)
