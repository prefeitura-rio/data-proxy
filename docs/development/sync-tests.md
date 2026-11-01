# Sync tests

The `sync` suite tests the sync code, the SQL templates and the console SQL. This page describes the unit and the integration tests of the suite. [Tests](tests.md) says how to run them.

The `sync` suite tests the sync code and the SQL templates. It runs with `pytest-xdist` on up to four workers. Each worker clones its own database from `test_template`, because the templates install functions that all workers would share. A session setup that every worker runs, such as the DBOS migration, takes a file lock (`one_worker_at_a_time` in `sync/tests/helpers/core.py`). Write a new test so that it uses only its own schema, and it runs on any worker.

A unit test checks one function, class, method or template with every input and edge case. The template tests are in `sync/tests/unit/test_templates_*.py`, one file for each family:

- `duckdb` runs the DuckDB templates on a real DuckDB with a file catalog, and the BigQuery template with a mock.
- `routing`, `filter` and `serving` run the request functions on the real test server.
- `access` and `maintenance` run the access policy, the schema and the reconcile templates on the real test server.
- `roles` runs the roles and the `rbac` templates on the real test server.

The `rbac` tests in `test_templates_roles.py` include Hypothesis tests. They generate random rules files and compare the answers of `rbac.can()` and `rbac.mine()` with a reference that the test computes in Python. A property runs 300 examples by default, and `RBAC_EXAMPLES` sets another number.

Every template that has some logic has a test that renders it, runs it, and checks the result.

The console SQL has its own tests, on the real server and in the test transaction. The fixture `console_api` (`sync/tests/fixtures/console.py`) builds the console from the real migrations and the real templates, over the DBOS tables and the rules of `rbac.toml`. The tests in `test_console_api.py`, `test_console_config.py`, `test_console_templates.py` and `test_migrations.py` cover these parts:

- the functions that the console calls
- the views and their scope by schema
- the import and its preview, and the history
- the migrations on data of an older shape

The file `proxy/tests/unit/fixtures/console_config.json` is the document that `console.config()` writes for fixed rows. A pytest case checks that the database still writes it, and the Lua specs check that the proxy takes it. A change to one side breaks the other side.

`sync/tests/integration/test_pipeline.py` runs the body of `run_sync` with its real steps, a fake source, and the PostgreSQL, S3 and DuckLake of the test stack. It reaches no DBOS runtime, Kubernetes, Redis or PostgREST.

An integration test (`sync/tests/integration/`) checks a behavior that crosses units, such as the catalog, the state and S3.

The suite doesn't test the API schemas, the database roles, or what a token may do. The end-to-end suite tests those ([Roles](../security/roles.md#how-the-rules-are-tested) lists what it covers).

<!-- nav:start -->
---

[Previous: Test stack](test-stack.md) | [Next: Checks](checks.md) | [Index](tests.md) | [Home](../index.md)
<!-- nav:end -->
