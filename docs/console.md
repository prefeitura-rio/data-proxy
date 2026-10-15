# Console

The console is the admin web app of the data proxy. It lives in `console/`. It's a SvelteKit project with server-side rendering, written in TypeScript and managed with pnpm. Today it has a placeholder page, and the next steps build the pages on top of the database.

## Views

The `admin` schema holds the views that the console reads through PostgREST. `console/migrations/00001_create_admin_views.sql` creates them, and `sync/tests/integration/test_admin_views.py` tests them.

| View | Content |
| --- | --- |
| `runs` | Sync runs: status, queue, schedule, who started it, the times, and the number of tasks and failed tasks. |
| `tasks` | The dump and publish workflows of a run. |
| `steps` | The steps of a run and its tasks, with the times and a `failed` flag. |
| `logs` | The log lines of a run and its tasks, read from the portable DBOS log stream. |
| `queues` | The queue settings and the number of enqueued, pending, and delayed workflows. |
| `schedules` | The schedules with their cron expression and status. |
| `errors` | The errors that the sync recorded, newest first. |
| `overview` | One row for each schema: its tables, how many have committed state, its last run, and the runs, failed runs, and errors of the last 24 hours. The dashboard reads it in one request. |

## Configuration tables

The sync configuration is data in three tables of the `admin` schema. `console/migrations/00006_create_config.sql` creates them, and `sync/tests/integration/test_admin_config.py` tests the rules.

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

The proxy reads a smaller document, `admin.config()`: the table names and their `cache_ttl`. The chart grants it to the anonymous role, and the proxy refuses the `admin` profile from outside, so only the cluster can call it. A trigger on `schemas`, `tables`, and `rls` queues a `reload_proxy` workflow after every statement. The queue deduplicates the workflow, so a burst of edits shares one reload, and an editor never sees an error from it. The sync worker then sends a reload request to every proxy pod ([Proxy](proxy.md#table-settings-from-the-configuration)).

## Permission levels

The JWT of a user carries two claims. The `role` claim holds the level, and PostgREST switches to that database role. The `schemas` claim lists the schemas that the user manages. The chart declares the roles as CNPG managed roles, and it makes the PostgREST authenticator a member of every level. The migrations only grant privileges on the objects that they create. `scripts/db.nu` creates the same roles on the test server. Each level inherits the levels below it.

| Role | Can do |
| --- | --- |
| `console_viewer` | Read the views and the configuration of its schemas. |
| `console_operator` | Viewer, plus start a sync run for its schemas. |
| `console_editor` | Viewer, plus insert and update the configuration of its schemas. Names and keys never change. |
| `console_admin` | Operator and editor, plus delete configuration, start any run, and change queues and schedules. |

The admin has full powers. It ignores the `schemas` claim and sees and changes everything. Every other level sees only the schemas in its claim, and a token without the claim sees nothing. The database enforces this, not the console:

- Row-level security filters `schemas`, `tables`, and `rls` by the schema of each row. An editor can write a row only for a schema in its claim. A claim can name a schema that doesn't exist yet, so an editor can create it.
- The views `tasks`, `logs`, `errors`, `health`, `synced`, and `overview` show the rows of the schemas in the claim. A log line without a schema is for the admin only.
- When a run starts, the engine records the schemas that it covers, and the schema of each dump task, in `data_proxy.scope`. `runs`, `tasks`, and `steps` show a user the runs and tasks that cover one of its schemas. The counts of tasks in a run count only the tasks that the user sees. A task that has no row yet, such as a task that was just enqueued, is for the admin only until the row arrives.
- `GET /rpc/sync_config` returns the schemas of the claim, and the admin gets the whole document.
- Queues and schedules serve every schema, so only the admin changes them. Every level reads them.

PostgREST copies the claims into session settings with the pre-request function. The chart grants that function to `console_viewer`, which every level inherits. `admin.is_admin()` and `admin.mine(schema)` read the role setting and the `app.claim_schemas` setting. They work inside `SECURITY DEFINER` functions too, where `current_user` is the owner.

A viewer can read the configuration tables, but only the rows of its schemas, and it can't change anything. It can't read the DBOS tables or the state tables behind the views. Operators call `POST /rpc/start_sync` with the optional arrays `schemas` and `tables`. Empty arrays mean every schema of the token, and for the admin every schema. `admin.start_scope(schemas, tables)` shows which schemas a run would cover. A schema or a table outside the claim gets a 403, and so does a table that doesn't exist, so a name tells nothing. An unknown schema in the claim gets a 400 error. A schema that the provision job hasn't created yet, with its access policy, gets a 409 with the reason. A schema that an editor adds is ready within a minute or two. An admin run with no schemas covers every schema and skips this check. The function enqueues `run_sync` in the `sync` queue with portable JSON arguments and the name from the token, and the worker takes it from there. `sync/tests/integration/test_admin_scope.py` tests the scope, and `sync/tests/integration/test_admin_start.py` runs a real DBOS worker against `start_sync`.

The admin changes a queue with `PATCH /queues?name=eq.<name>` and a schedule with `PATCH /schedules?name=eq.<name>`. It can edit the limits and the polling interval of a queue, and the status, the cron expression, the time zone, and the backfill flag of a schedule. A worker reads queue settings on every poll and checks schedules every 30 seconds, so a change needs no restart. The database refuses values that DBOS would reject: a limit that isn't positive, a worker limit above the total, a status other than `ACTIVE` or `PAUSED`, an unknown time zone, and a cron expression that doesn't have five or six fields. It can't check the meaning of each field, so a value such as `99 * * * *` passes.

## Tokens from the identity provider

The console sends the access token of the user to PostgREST. PostgREST checks it against the keys of the identity provider, like it does for the data API. The token needs two claims besides the standard ones:

| Claim | Holds |
| --- | --- |
| `role` | One of `console_viewer`, `console_operator`, `console_editor`, `console_admin`. Map it from the groups of the user, and give the highest level that the user has. |
| `schemas` | The list of schemas that the user manages, such as `["sales", "hr"]`. An admin can have an empty list, because the admin ignores it. |

The `preferred_username` claim names the user in `updated_by` and `started_by`. The k3d Keycloak realm has one client for each level, `console_viewer` to `console_admin`, with fixed claims. They give tokens to the tests and to the console in development. The secret is `test-secret`, and each client uses the `client_credentials` grant.

## Reading logs and runs

The console polls. A page reads `GET /logs?run_id=eq.<id>&logged_at=gte.<last>&order=logged_at.asc` every few seconds, and it drops the lines that it already has, using the pair of `workflow_id` and `position`. It reads `GET /runs?id=eq.<id>` and `GET /tasks?run_id=eq.<id>` for the status. `GET /overview` gives the dashboard in one request.

## The query tab

Admins and schema admins query the synced data in the browser, with DuckDB compiled to WebAssembly. The files stay in object storage, and the browser reads them directly.

1. The console server calls `GET /rpc/table_files?schema_name=<schema>&table_name=<source table>` with the token of the user. `admin.table_files` is for `console_editor` and `console_admin`. It checks the schemas of the token, and it answers 403 for a schema or a table outside them, and for a name that doesn't exist. The engine generates `dl_table_files` in every schema, and the function calls it. It lists the Parquet files of the table at its latest snapshot, with the size of each and its delete file, if any.
2. The console server signs a short-lived `GET` link for each file with its own read-only storage credentials, and it sends the links to the browser. PostgREST can't sign links, so the database never holds storage credentials.
3. The browser reads the files with `read_parquet`. A delete file means that some rows are gone, and the browser can't apply it, so the page must say so and not show wrong rows. A schema with encrypted files answers 409, because the browser has no key.

The files hold every row, and row-level security doesn't apply to them. That's the reason why only editors and admins can list them. The bucket needs a CORS rule for the origin of the console, or the browser can't read the files. The rule allows `GET` and `HEAD`, allows the `Range` header, and exposes `Content-Length`, `Content-Range`, `ETag`, and `Accept-Ranges`. The chart doesn't set it, because the storage differs between deployments: SeaweedFS, S3, and GCS each use their own command.

## HTTP interface

The console talks to PostgREST only. `sync/tests/integration/test_console_http.py` tests this contract against the PostgREST process of `devenv test`.

- Send `Authorization: Bearer <JWT>` with the permission level in the `role` claim and the schemas of the user in the `schemas` claim. Send `Accept-Profile: admin` on reads and `Content-Profile: admin` on writes, because PostgREST also serves the schemas of the synced data.
- Send `Prefer: return=representation` to get the written row back.
- Reads are `GET /runs`, `GET /schemas`, and the other views. The configuration is `POST`, `PATCH` and `DELETE` on `/schemas`, `/tables` and `/rls`, and `GET /rpc/sync_config` reads the whole document. Queues and schedules are `PATCH /queues?name=eq.<name>` and `PATCH /schedules?name=eq.<name>`, for the admin. The functions are `POST /rpc/start_sync`, `POST /rpc/import_config`, and `GET /rpc/table_files`. `GET /rpc/is_admin` tells the console whether to show the admin actions.

| Status | Meaning |
| --- | --- |
| 200, 201 | Done, with the row or the value in the body. |
| 204 | Done, and there is nothing to return. `import_config` answers this way. |
| 400 | The value is wrong: a rule that the configuration tables enforce, a bad queue limit, a bad cron expression, or an unknown time zone. The body has the reason. |
| 401 | No token, or an anonymous one. |
| 409 | The row clashes with another row: a duplicate key, a table in two schemas, or a schema that doesn't exist. `start_sync` also answers 409 for a schema that isn't provisioned yet. |
| 403 | The level of the token isn't enough. |

## Direction

The database is the API. The console reads these views, and it writes through the read/write PostgREST with the same pattern as `access_policy`: a database role per capability and grants. The sync configuration is data in proper tables, and one endpoint serves it to the engine and the proxy. A SQL function starts a run with `dbos.enqueue_workflow`, so the console needs no DBOS client.

## Develop

[Development](development.md#console) lists the commands of the `console` profile. The migrations are in [Development](development.md#database-migrations).
