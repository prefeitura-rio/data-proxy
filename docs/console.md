# Console

The console is the admin web app of the data proxy. It lives in `console/`. It's a SvelteKit project with server-side rendering, written in TypeScript and managed with pnpm. Today it has a placeholder page, and the next steps build the pages on top of the database.

## Views

The `admin` schema holds the views that the console reads through PostgREST. `console/migrations/00002_create_admin_views.sql` creates them, and `sync/tests/integration/test_admin_views.py` tests them.

| View | Content |
| --- | --- |
| `runs` | Sync runs: status, queue, schedule, who started it, the times, and the number of tasks and failed tasks. |
| `tasks` | The dump and publish workflows of a run. |
| `steps` | The steps of a run and its tasks, with the times and a `failed` flag. |
| `logs` | The log lines of a run and its tasks, read from the portable DBOS log stream. |
| `queues` | The queue settings and the number of enqueued, pending, and delayed workflows. |
| `schedules` | The schedules with their cron expression and status. |
| `errors` | The errors that the sync recorded, newest first. |

## Configuration tables

The sync configuration is data in three tables of the `admin` schema. `console/migrations/00007_create_config.sql` creates them, and `sync/tests/integration/test_admin_config.py` tests the rules.

| Table | Holds |
| --- | --- |
| `schemas` | One row per synced schema: `name`, `source`, `settings`, `claim`, `encrypted`. |
| `tables` | One row per source table: `name`, its `schema`, `strategy`, `n`, `fallback`, `cache_ttl`, `partitioning`, `sort`. |
| `rls` | One row per rule: the `table_name`, the `column_name`, and the `unit_type`. |

Every row records `updated_at` and `updated_by`. The database sets both. The name comes from the `preferred_username` claim of the token, then from `sub`.

Constraints replace a validation step:

- A schema name uses lowercase letters, digits and underscores, and it starts with a letter.
- A table name is unique in the whole configuration, and unique in its schema by its last part.
- `n` and `fallback` need the `partitioned` strategy, and `n` must be positive.
- A table needs a schema that exists, and an `rls` rule needs a schema with a `claim`.
- A schema can't lose its `claim` while one of its tables has a rule.

The engine still checks the rules that belong to a source, such as the form of a table name, when a run starts.

Two read-only views compare the configuration with the system. `health` shows, for each schema, whether the PostgreSQL schema, its `access_policy`, its `policy_writer_<schema>` role, and its views exist. `synced` shows, for each table, whether a sync committed its state, with the signature and the number of partitions.

## The configuration endpoint

`GET /rpc/sync_config` returns the whole configuration in the shape of `sync.json`. Every level can call it. The engine and the proxy read this one document. `admin.sync_config()` builds it from the tables, and it leaves out every default value: no `claim` without a claim, no `rls` without rules, and no `fallback` unless it's true. The tables come out sorted by name, and the rules keep their order.

A new setup can load an existing file. Admins call `POST /rpc/import_config` with `{"document": <sync.json>}`. The function inserts or updates the schemas and tables of the document, and it replaces the rules of each table in the document. It never deletes a schema or a table, and it never moves a table to another schema. The call is one transaction, so a row the constraints refuse leaves nothing behind. `sync/tests/integration/test_admin_sync_config.py` proves that a document comes back unchanged after an import and an export, and that the engine accepts the result.

One limit: the export drops a `null` value inside the `settings` of a source, because the export removes every `null` of the document.

## Permission levels

The JWT of a user carries the level in its `role` claim, and PostgREST switches to that database role. The chart declares the roles as CNPG managed roles, and it makes the PostgREST authenticator a member of every level. The migrations only grant privileges on the objects that they create. `scripts/db.nu` creates the same roles on the test server. Each level inherits the levels below it.

| Role | Can do |
| --- | --- |
| `console_viewer` | Read the views. |
| `console_operator` | Viewer, plus start a sync run and change queues and schedules. |
| `console_editor` | Viewer, plus insert and update the configuration. Names and keys never change. |
| `console_admin` | Operator and editor, plus delete configuration. A schema with tables can't be deleted, and a deleted table takes its rules along. |

A viewer can't read the tables behind the views, and it can't change anything. Operators call `POST /rpc/start_sync` with the optional arrays `schemas` and `tables`. Empty arrays mean every table. The function refuses an unknown schema with a 400 error. The function enqueues `run_sync` in the `sync` queue with portable JSON arguments and the name from the token, and the worker takes it from there. `sync/tests/integration/test_admin_start.py` runs a real DBOS worker against it.

Operators change a queue with `PATCH /queues?name=eq.<name>` and a schedule with `PATCH /schedules?name=eq.<name>`. They can edit the limits and the polling interval of a queue, and the status, the cron expression, the time zone, and the backfill flag of a schedule. A worker reads queue settings on every poll and checks schedules every 30 seconds, so a change needs no restart. The database refuses values that DBOS would reject: a limit that isn't positive, a worker limit above the total, a status other than `ACTIVE` or `PAUSED`, an unknown time zone, and a cron expression that doesn't have five or six fields. It can't check the meaning of each field, so a value such as `99 * * * *` passes.

## HTTP interface

The console talks to PostgREST only. `sync/tests/integration/test_console_http.py` tests this contract against the PostgREST process of `devenv test`.

- Send `Authorization: Bearer <JWT>` with the permission level in the `role` claim. Send `Accept-Profile: admin` on reads and `Content-Profile: admin` on writes, because PostgREST also serves the schemas of the synced data.
- Send `Prefer: return=representation` to get the written row back.
- Reads are `GET /runs`, `GET /schemas`, and the other views. The configuration is `POST`, `PATCH` and `DELETE` on `/schemas`, `/tables` and `/rls`, and `GET /rpc/sync_config` reads the whole document. Queues and schedules are `PATCH /queues?name=eq.<name>` and `PATCH /schedules?name=eq.<name>`. The functions are `POST /rpc/start_sync` and `POST /rpc/import_config`.

| Status | Meaning |
| --- | --- |
| 200, 201 | Done, with the row or the value in the body. |
| 204 | Done, and there is nothing to return. `import_config` answers this way. |
| 400 | The value is wrong: a rule that the configuration tables enforce, a bad queue limit, a bad cron expression, or an unknown time zone. The body has the reason. |
| 401 | No token, or an anonymous one. |
| 409 | The row clashes with another row: a duplicate key, a table in two schemas, or a schema that doesn't exist. |
| 403 | The level of the token isn't enough. |

## Direction

The database is the API. The console reads these views, and it writes through the read/write PostgREST with the same pattern as `access_policy`: a database role per capability and grants. The sync configuration is data in proper tables, and one endpoint serves it to the engine and the proxy. A SQL function starts a run with `dbos.enqueue_workflow`, so the console needs no DBOS client.

## Develop

[Development](development.md#console) lists the commands of the `console` profile. The migrations are in [Development](development.md#database-migrations).
