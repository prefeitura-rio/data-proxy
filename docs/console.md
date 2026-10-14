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

## Permission levels

The JWT of a user carries the level in its `role` claim, and PostgREST switches to that database role. The chart declares the roles as CNPG managed roles, and it makes the PostgREST authenticator a member of every level. The migrations only grant privileges on the objects that they create. `scripts/db.nu` creates the same roles on the test server. Each level inherits the levels below it.

| Role | Can do |
| --- | --- |
| `console_viewer` | Read the views. |
| `console_operator` | Viewer, plus start a sync run and change queues and schedules. |
| `console_editor` | Viewer, plus edit the sync configuration. |
| `console_admin` | Operator and editor, plus delete configuration. |

A viewer can't read the tables behind the views, and it can't change anything. Operators call `POST /rpc/start_sync` with the optional arrays `schemas` and `tables`. Empty arrays mean every table. The function enqueues `run_sync` in the `sync` queue with portable JSON arguments and the name from the token, and the worker takes it from there. `sync/tests/integration/test_admin_start.py` runs a real DBOS worker against it.

Operators change a queue with `PATCH /queues?name=eq.<name>` and a schedule with `PATCH /schedules?name=eq.<name>`. They can edit the limits and the polling interval of a queue, and the status, the cron expression, the time zone, and the backfill flag of a schedule. A worker reads queue settings on every poll and checks schedules every 30 seconds, so a change needs no restart. The database refuses values that DBOS would reject: a limit that isn't positive, a worker limit above the total, a status other than `ACTIVE` or `PAUSED`, an unknown time zone, and a cron expression that doesn't have five or six fields. It can't check the meaning of each field, so a value such as `99 * * * *` passes.

## HTTP interface

The console talks to PostgREST only. `sync/tests/integration/test_console_http.py` tests this contract against the PostgREST process of `devenv test`.

- Send `Authorization: Bearer <JWT>` with the permission level in the `role` claim. Send `Accept-Profile: admin` on reads and `Content-Profile: admin` on writes, because PostgREST also serves the schemas of the synced data.
- Send `Prefer: return=representation` to get the written row back.
- Reads are `GET /runs`, `GET /schemas`, and the other views. Queues and schedules are `PATCH /queues?name=eq.<name>` and `PATCH /schedules?name=eq.<name>`. The function is `POST /rpc/start_sync`.

| Status | Meaning |
| --- | --- |
| 200, 201 | Done, with the row or the value in the body. |
| 204 | Done, and there is nothing to return. |
| 400 | The value is wrong: a bad queue limit, a bad cron expression, or an unknown time zone. The body has the reason. |
| 401 | No token, or an anonymous one. |
| 403 | The level of the token isn't enough. |

## Direction

The database is the API. The console reads these views, and it writes through the read/write PostgREST with the same pattern as `access_policy`: a database role per capability and grants. The sync configuration is moving into proper tables, and one endpoint will serve it to the engine and the proxy. A SQL function starts a run with `dbos.enqueue_workflow`, so the console needs no DBOS client.

## Develop

[Development](development.md#console) lists the commands of the `console` profile. The migrations are in [Development](development.md#database-migrations).
