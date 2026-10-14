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
| `revisions` | Every configuration revision with its schema and table counts, and its validation. |
| `drafts` | The revisions that were never active. Editors write them. |
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

A viewer can't read the tables behind the views, and it can't change anything. Editors write drafts through the `drafts` view. They can set the comment and the document, and the author always comes from the token: the `preferred_username` claim, then `sub`. A change to the document clears the validation of the draft.

Admins call `POST /rpc/activate_revision` with a revision `id`. The function refuses a revision that hasn't passed validation with a 400 error, and an unknown revision with a 404 error. It replaces the active revision in one transaction. A rollback is the same call with an earlier revision. Operators call `POST /rpc/start_sync` with the optional arrays `schemas` and `tables`. Empty arrays mean every table. The function refuses an unknown schema with a 400 error. It enqueues `run_sync` in the `sync` queue with portable JSON arguments and the name from the token, and the worker takes it from there. `sync/tests/integration/test_admin_start.py` runs a real DBOS worker against it.

Operators change a queue with `PATCH /queues?name=eq.<name>` and a schedule with `PATCH /schedules?name=eq.<name>`. They can edit the limits and the polling interval of a queue, and the status, the cron expression, the time zone, and the backfill flag of a schedule. A worker reads queue settings on every poll and checks schedules every 30 seconds, so a change needs no restart. The database refuses values that DBOS would reject: a limit that isn't positive, a worker limit above the total, a status other than `ACTIVE` or `PAUSED`, an unknown time zone, and a cron expression that doesn't have five or six fields. It can't check the meaning of each field, so a value such as `99 * * * *` passes.

## Direction

The database is the API. The console reads these views, and it writes through the read/write PostgREST with the same pattern as `access_policy`: a database role per capability, grants and row-level security. The sync worker does what SQL can't: it validates a draft configuration with its own model, and it runs commands such as a sync run. A SQL function starts a run with `dbos.enqueue_workflow`, so the console needs no DBOS client.

## Develop

[Development](development.md#console) lists the commands of the `console` profile. The migrations are in [Development](development.md#database-migrations).
