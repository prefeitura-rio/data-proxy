# /// script
# dependencies = ["marimo", "polars"]
# ///

import marimo

app = marimo.App()

with app.setup:
    import json

    import marimo as mo
    import polars as pl
    from pyodide.code import run_js

    PREVIEW_ROWS = 50


@app.function
async def sql(query: str, limit: int = 100_000) -> pl.DataFrame:
    """Run SQL in DuckDB-WASM on the tables of the grant and return at most limit rows.

    The scan stays in DuckDB-WASM, so a large table never fills the Python heap.
    """
    try:
        rows = json.loads(
            await run_js(
                f"globalThis.dataProxy.query({json.dumps(query)}, {int(limit)})"
            )
        )
    except Exception as error:
        if "403" in str(error) or "Forbidden" in str(error):
            raise RuntimeError(
                "The links to the files expired. Open the notebook again from the Query tab."
            ) from error
        raise

    return pl.DataFrame(rows, infer_schema_length=None)


@app.cell
async def _():
    # Start DuckDB-WASM, then wait for the grant: the console sends the signed links of the tables that the user chose.
    await run_js(
        "import(self.location.origin + '/helper.js').then((module) => module.start())"
    )
    await run_js("globalThis.dataProxy.first")

    views = await sql("SELECT view_name FROM duckdb_views() WHERE NOT internal")
    tables = [row["view_name"] for row in views.iter_rows(named=True)]

    mo.stop(
        not tables,
        mo.md(
            "**No table has data files yet.** Sync the tables, then open the notebook again."
        ),
    )

    mo.md("Tables you chose: " + ", ".join(f"`{name}`" for name in tables))
    return (tables,)


@app.cell
async def _(tables):
    # A notebook has a fixed list of cells, so one cell shows a section for each table that the user chose.
    for name in tables:
        quoted = '"' + name.replace('"', '""') + '"'
        preview = await sql(f"SELECT * FROM {quoted}", limit=PREVIEW_ROWS)

        mo.output.append(
            mo.vstack(
                [
                    mo.md(f"### `{name}`: the first {PREVIEW_ROWS} rows"),
                    mo.ui.table(preview, selection=None),
                ]
            )
        )


if __name__ == "__main__":
    app.run()
