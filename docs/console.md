# Console

The console is the admin web app of the data proxy. It lives in `console/`. It's a SvelteKit project with server-side rendering, written in TypeScript and managed with pnpm. Today it has a placeholder page, and the next steps build the pages on top of the database.

## Views

The `admin` schema holds the views that the console reads through PostgREST. `console/migrations/00004_create_admin_views.sql` creates them, and `sync/tests/integration/test_admin_views.py` tests them.

| View | Content |
| --- | --- |
| `runs` | Sync runs: status, queue, schedule, who started it, the times, and the number of tasks and failed tasks. |
| `tasks` | The dump and publish workflows of a run. |
| `steps` | The steps of a run and its tasks, with the times and a `failed` flag. |
| `logs` | The log lines of a run and its tasks, read from the portable DBOS log stream. |
| `queues` | The queue settings and the number of enqueued, pending, and delayed workflows. |
| `schedules` | The schedules with their cron expression and status. |
| `revisions` | Every configuration revision with its schema and table counts. |
| `config` | The active configuration document. |
| `schemas` | The configured and provisioned schemas with their status, and which objects exist: the schema, `access_policy`, the `policy_writer_<schema>` role, and the views. |
| `tables` | The configured tables with their strategy and the committed sync state. |
| `errors` | The errors that the sync recorded, newest first. |

## Permission levels

The JWT of a user carries the level in its `role` claim, and PostgREST switches to that database role. The chart declares the roles as CNPG managed roles, and it makes the PostgREST authenticator a member of every level. The migrations only grant privileges on the objects that they create. `scripts/db.nu` creates the same roles on the test server. Each level inherits the levels below it.

| Role | Can do |
| --- | --- |
| `console_viewer` | Read the views. |
| `console_operator` | Viewer, plus start a sync run and change queues and schedules. |
| `console_editor` | Viewer, plus create and edit draft configurations. |
| `console_admin` | Operator and editor, plus activate a revision and provision schemas. |

Today only the viewer grants exist. A viewer can't read the tables behind the views, and it can't change anything. The other levels get their grants together with the functions they call.

## Direction

The database is the API. The console reads these views, and it writes through the read/write PostgREST with the same pattern as `access_policy`: a database role per capability, grants and row-level security. The sync worker does what SQL can't: it validates a draft configuration with its own model, and it runs commands such as a sync run. A SQL function starts a run with `dbos.enqueue_workflow`, so the console needs no DBOS client.

## Develop

[Development](development.md#console) lists the commands of the `console` profile. The migrations are in [Development](development.md#database-migrations).
