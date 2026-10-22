# Control a run and a schedule

A user with `control_sync` can cancel a sync run, resume it and run it again, and pause or resume a schedule.

The functions `console.cancel_run(run)`, `console.resume_run(run)`, `console.retry_run(run)`, `console.pause_schedule(schedule_name)` and `console.resume_schedule(schedule_name)` check the permission and the schemas of the token. They're in the template `create_console_functions`.

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

A schedule serves every schema. Pausing or resuming it therefore needs a token whose `schemas` claim lists every schema of `console.schemas`. A narrower claim returns 403.

The setting `LISTEN_QUEUES` limits the queues that one worker listens to. It contains queue names separated by commas, and an empty value means every queue. The test stack runs a second worker with `LISTEN_QUEUES=control`, so the browser tests run the control workflows and never a sync run.

<!-- nav:start -->
---

[Previous: Database views](views.md) | [Next: Configuration](configuration.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
