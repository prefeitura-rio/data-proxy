# /// script
# dependencies = ["marimo", "pandas"]
# ///

import marimo

app = marimo.App()


@app.cell
async def _():
    import json

    import marimo as mo
    from pyodide.code import run_js

    await run_js(
        "import(self.location.origin + '/helper.js').then((module) => module.start())"
    )
    return json, mo, run_js


@app.cell
async def _(json, run_js):
    async def ready():
        """Wait until the console has sent the first grant."""
        await run_js("globalThis.dataProxy.first")

    async def q(sql, limit=100_000):
        """Run SQL on the tables of the schema and return at most limit rows as a DataFrame."""
        import pandas as pd

        try:
            rows = json.loads(
                await run_js(
                    f"globalThis.dataProxy.query({json.dumps(sql)}, {int(limit)})"
                )
            )
        except Exception as error:
            if "403" in str(error) or "Forbidden" in str(error):
                raise RuntimeError(
                    "The links to the files expired. Open the notebook again from the Query tab."
                ) from error
            raise

        return pd.DataFrame(rows)

    return q, ready


@app.cell
async def _(q, ready):
    await ready()
    tables = await q("SELECT view_name AS name FROM duckdb_views() WHERE NOT internal")
    tables  # noqa: B018 -- marimo shows the last expression of a cell


if __name__ == "__main__":
    app.run()
