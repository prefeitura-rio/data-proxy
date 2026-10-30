# Control a run and a schedule

A user with `control_sync` can cancel a sync run, resume it and run it again, and pause or resume a schedule or change its cron.

The functions `console.cancel_run(run)`, `console.resume_run(run)`, `console.retry_run(run)`, `console.pause_schedule(schedule_name)`, `console.resume_schedule(schedule_name)` and `console.set_schedule(schedule_name, cron)` check the permission and the schemas of the token. They're in the template `create_console_functions`.

Cancel, resume and the schedule actions enqueue a workflow in the queue `control`, and the sync worker runs it with the DBOS API. [Console layer](../architecture/console.md#run-control) shows the sequence. A sync run accepts an action only in the right status:

| Action | Function | Needs | Allowed statuses | Result |
| --- | --- | --- | --- | --- |
| Cancel | `cancel_run` | `control_sync` | `PENDING`, `ENQUEUED`, `DELAYED` | Stops the sync run and its tasks at the next step. |
| Resume | `resume_run` | `control_sync` | `CANCELLED`, `MAX_RECOVERY_ATTEMPTS_EXCEEDED` | Puts the sync run back in the `sync` queue, so the concurrency of that queue still applies. |
| Run again | `retry_run` | `control_sync` and `start_sync` | `SUCCESS`, `ERROR`, `CANCELLED`, `MAX_RECOVERY_ATTEMPTS_EXCEEDED` | Starts a new sync run through `start_sync`, for the schemas of the old sync run that the token reaches. |

| Case | Answer |
| --- | --- |
| The token lacks `control_sync` | 403 |
| The sync run is in another status | 409 |
| The token doesn't see the sync run | 404 |

## Schedules

Each schema has one schedule, named `sync-<schema>`. It starts a sync run for that schema only, at the cron of the schedule. A new schema gets the cron of `SYNC_SCHEDULE` (`0 2 * * *`). The workflow `reconcile_schedules` makes the schedules match the schemas. It runs in its own queue, `schedules`, one at a time.

It runs at the start of a worker, and after each insert or delete in `console.schemas`. It also deletes the schedule of a removed schema, and the old global schedule `sync`.

The Sync tab lists the schedules of the schemas in the claim. Its Edit dialog changes the cron, which has 5 fields, or 6 with the seconds first. The times are UTC. The schedule keeps its paused state, its context and its queue. There is no delete button, because every schema keeps one schedule. Pause the schedule to stop its runs.

A schedule doesn't backfill. A run whose time passes while no sync worker runs is skipped, so start it by hand from the Sync tab.

A schedule action needs the schema of the schedule in the `schemas` claim. A token without that schema gets 404, as for a sync run. A bad cron returns 400, and the worker never sees it.

The setting `LISTEN_QUEUES` limits the queues that one worker listens to. It contains queue names separated by commas, and an empty value means every queue. The test stack runs a second worker with `LISTEN_QUEUES=control,schedules`. The browser tests therefore run the control workflows and the schedule reconcile, and never a sync run.

<!-- nav:start -->
---

[Previous: Database views](views.md) | [Next: Configuration](configuration.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
