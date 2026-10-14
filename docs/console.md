# Console

The console is the admin web app of the data proxy. It lives in `console/`. It's a SvelteKit project with server-side rendering, written in TypeScript and managed with pnpm. Today it has a placeholder page, and the next steps build the pages on top of the database.

## Views

The `admin` schema holds the views that the console reads through PostgREST. `console/migrations/00004_create_admin_views.sql` creates them, and `sync/tests/integration/test_admin_views.py` tests them.

| View | Content |
| --- | --- |
| `runs` | Sync runs: status, queue, schedule, who started it, and the times. |
| `tasks` | The dump and publish workflows of a run. |
| `steps` | The steps of a run and its tasks, with the times and a `failed` flag. |
| `logs` | The log lines of a run and its tasks, read from the portable DBOS log stream. |
| `queues` | The queue settings and the number of enqueued, pending, and delayed workflows. |
| `schedules` | The schedules with their cron expression and status. |
| `revisions` | Every configuration revision with its schema and table counts. |
| `config` | The active configuration document. |
| `schemas` | The configured and provisioned schemas with their status. |
| `tables` | The configured tables with their strategy and the committed sync state. |
| `errors` | The errors that the sync recorded, newest first. |

## Direction

The database is the API. The console reads these views, and it writes through the read/write PostgREST with the same pattern as `access_policy`: a database role per capability, grants and row-level security. The sync worker does what SQL can't: it validates a draft configuration with its own model, and it runs commands such as a sync run. A SQL function starts a run with `dbos.enqueue_workflow`, so the console needs no DBOS client.

## Develop

[Development](development.md#console) lists the commands of the `console` profile. The migrations are in [Development](development.md#database-migrations).
