# /// script
# dependencies = ["marimo", "polars"]
# ///

import marimo

app = marimo.App()

with app.setup(hide_code=True):
    import types

    # marimo packs the packages that the notebook imports, and lib.py needs this one.
    import polars  # noqa: F401
    from pyodide.code import run_js
    from pyodide.http import open_url

    lib = types.ModuleType("lib")
    # lib.py is a file of the site that this notebook runs on, so it is as trusted as the notebook.
    exec(open_url(run_js("self.location.origin") + "/lib.py").read(), lib.__dict__)  # noqa: S102
    query = lib.query


# The site writes one cell like this for each table that the user chose, in place of this one.
@app.cell
async def _():
    await query('select * from "{dataset}" limit 15')


if __name__ == "__main__":
    app.run()
