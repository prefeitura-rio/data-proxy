# Database views

The console reads the database through views and a few functions. These sections list the views, the logs and sync runs views, and the HTTP interface.

## Views

The `console` schema contains the views that the console reads through PostgREST. `console/migrations/00001_create_console_views.sql` creates them, and the migrations `00004_create_status_views.sql`, `00005_scope_by_schema.sql` and `00008_add_run_schemas.sql` add or replace some of them. The `console` scenario of the end-to-end suite (`k6/scenarios/console.js`) tests them.

| View | Content |
| --- | --- |
| `runs` | Sync runs: status, queue, schedule, who started it, the times, the number of tasks and failed tasks, and the schemas of the token that the sync run covers. |
| `tasks` | The dump and publish workflows of a sync run. |
| `steps` | The steps of a sync run and its tasks, with the times and a `failed` flag. |
| `logs` | The log lines of a sync run and its tasks, read from the portable DBOS log stream. |
| `queues` | The queue settings and the number of enqueued, pending, and delayed workflows. |
| `schedules` | The schedules with their cron expression and status. |
| `errors` | The errors that the sync recorded, newest first. |
| `overview` | One row for each schema: its tables, how many have committed state, its last sync run, and the sync runs, failed sync runs, and errors of the last 24 hours. The dashboard reads it in one request. |

The maintenance job deletes a sync run with its tasks and logs after it finishes and `jobs.maintenance.workflowRetentionDays` days pass (90 by default). The views then no longer show it. A sync run that isn't finished stays.

## Reading logs and runs

The run page streams the log. The route `/sync/runs/<id>/logs` sends server-sent events. Each second it reads `GET /runs?id=eq.<id>` for the status and `GET /logs?run_id=eq.<id>&logged_at=gte.<last>&order=logged_at.asc` for the lines. It sends each line once, using the pair of `workflow_id` and `position`.

The page has a Live logs switch, which is on while the sync run is active. Turn it off to stop the stream and the scrolling, and turn it on again to continue from the last line. The stream ends when the sync run finishes.

It reads `GET /runs?id=eq.<id>` and `GET /tasks?run_id=eq.<id>` for the status. `GET /overview` returns the dashboard in one request.

## HTTP interface

The console talks to PostgREST only. The `console` scenario in `k6/scenarios/console.js` tests this contract against the cluster.

- Send `Authorization: Bearer <JWT>` with a `role` claim (one role of `rbac.toml`) and a `schemas` claim that lists the schemas of the user. Send `Accept-Profile: console` on reads and `Content-Profile: console` on writes, because PostgREST also serves the schemas of the synced data. The access tab sends the data schema as the profile.
- Send `Prefer: return=representation` to get the written row back.
- Reads are `GET /runs`, `GET /schemas`, and the other views. The configuration is `POST`, `PATCH` and `DELETE` on `/schemas`, `/tables` and `/rls`, and `GET /rpc/sync_config` reads the whole document. Queues and schedules are read-only: `GET /queues` and `GET /schedules`. The functions are `POST /rpc/start_sync`, `POST /rpc/import_config`, and `GET /rpc/table_files`. `GET /rpc/permissions` tells the console which controls to show.

| Status | Meaning |
| --- | --- |
| 200, 201 | Done, with the row or the value in the body. |
| 204 | Done, and there is nothing to return. `import_config` answers this way. |
| 400 | The value is wrong: a rule that the configuration tables enforce, a bad queue limit, a bad cron expression, or an unknown time zone. The body has the reason. |
| 401 | No token, or an anonymous one. |
| 409 | The row clashes with another row: a duplicate key, a table in two schemas, or a schema that doesn't exist. `start_sync` also answers 409 for a schema that isn't provisioned yet. |
| 403 | The token lacks the permission, or a row policy refuses the row. |

<!-- nav:start -->
---

[Previous: Theme](theme.md) | [Next: Control a run and a schedule](runs.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
