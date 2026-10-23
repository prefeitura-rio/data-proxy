# /// script
# dependencies = ["marimo", "polars"]
# ///

# The notebook of the browser test. It runs the helper and reports each check to the page.

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
        await run_js("globalThis.dataProxy.first")

    async def q(sql, limit=100_000):
        import polars as pl

        rows = json.loads(
            await run_js(f"globalThis.dataProxy.query({json.dumps(sql)}, {int(limit)})")
        )
        return pl.DataFrame(rows, infer_schema_length=None)

    def report(check, value):
        run_js(
            "new BroadcastChannel('status').postMessage("
            + json.dumps({"type": "check", "check": check, "value": value})
            + ")"
        )

    return q, ready, report


@app.cell
async def _(q, ready, report):
    await ready()
    counted = await q("SELECT count(*) AS n FROM people")
    report("rows", int(counted["n"][0]))
    capped = await q("SELECT * FROM range(200000) t(i)")
    report("default_limit", len(capped))
    raised = await q("SELECT * FROM range(200000) t(i)", limit=150_000)
    report("raised_limit", len(raised))


if __name__ == "__main__":
    app.run()
