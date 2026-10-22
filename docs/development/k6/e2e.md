# E2E layout

`k6/e2e.js` is the entry point. It runs the steps of every scenario in the order of their `stage` number.

Every helper is in `k6/lib/`, split by topic. The topics are `env`, `log`, `check`, `http`, `auth`, `wait` and `kubernetes`. They continue with `cluster`, `console`, `runs`, `policy`, `pipeline`, `rbac` and `loadtest`. `k6/lib/index.js` only re-exports the modules.

A scenario file in `k6/scenarios/` contains only its steps. `k6/load.js` contains only its orchestration (profiles, thresholds and route choice) and its iteration functions. The exceptions are the two functions of `load.js` that write to the metrics that the file creates.

A scenario file exports `steps`, a list of `{ scenario, name, stage, run }`. The `stage` numbers leave gaps (10, 20, ...), so a new step fits between two others. To add a scenario, create `k6/scenarios/<name>.js` and add one import and one `...<name>.steps` line to `k6/scenarios/index.js`. The suite doesn't run a scenario that `scenarios/index.js` doesn't list.

Every check has the name `scenario: description`, and one check asserts one condition. A scenario file gets `expect`, `expectEach` and `requirePrecondition` from `scenario("<name>")`. The helper adds the prefix and throws when a description already starts with the name of a scenario. It also logs a warning immediately when a check fails.

A step that throws doesn't stop the run. The orchestration records a failed check for it and goes on with the next step. A later step can then fail only because the failed step left its state half done. Read the first failure first.

Every line that the k6 scripts write has the same shape: `scenario=<name> step=<step> event=<event>`, then more `key=value` fields. k6 adds `level` and `source=console` itself. A script can't add fields of its own, so the fields sit inside `msg`. `info` and `warn` in `k6/lib/log.js` write the lines, and the helpers name the step for you. `load.js` writes `scenario=load`. The events are:

| Event | When | Fields |
| --- | --- | --- |
| `run_started`, `run_done`, `run_failed_checks` | The start and the end of `e2e.js`. The end lists the names of every failed check. | `scenarios`, `steps`, `count`, `checks` |
| `step_started`, `step_done`, `step_failed` | Around each step. A step that throws logs the error. | `seconds`, `error` |
| `check_failed` | At once, when a check fails. | `check`, and the last request that the suite sent before the check: `last_request`, `last_status`, `last_body` |
| `wait_started`, `waiting`, `wait_done`, `wait_timed_out` | A labelled `waitUntil`. `waiting` repeats every 30 seconds. | `label`, `timeout`, `elapsed`, `seconds` |
| `job_started`, `job_waiting`, `job_done`, `job_failed` | A Kubernetes Job. `job_waiting` repeats every 30 seconds. | `job`, `elapsed`, `seconds` |
| `kill_pod`, `reads_during_outage` | The resilience scenarios. | `pod`, `role`, `down`, `failed`, `samples` |
| `load_started`, `load_check_failed`, `load_check_failures_suppressed`, `autoscaling_observed` | `load.js`. It prints at most 25 failures for each virtual user, then says that it stopped (the metrics keep counting). The wait for the tables of the load test is a labelled wait. | `profile`, `executor`, `duration`, `route`, `reason`, `status`, `source`, `deployment`, `from`, `to` |

The last request in `check_failed` came before the check. It can be another request than the one that the check is about, so use it as a hint. To follow a run, filter the log: `rg 'event=(step_|check_failed|wait_timed_out|job_failed)'`. The summary at the end of the log is the output of k6 itself. The `cluster k6` commands stream the log of the runner while the test runs.

The order of the steps is fixed. The steps change shared state: the snapshot, the sync pods, the partition manifests, and the access policy. A later step relies on what an earlier step left, so run the whole suite after you move a step. The `failover` scenario runs last, because it kills the primary.

The k6 files are plain JavaScript with JSDoc types. An editor checks them through `k6/tsconfig.json` (`allowJs` and `checkJs`), and nothing builds them. `Containerfile.k6` copies `k6/` into the k6 image, and the TestRun starts `/scripts/e2e.js` or `/scripts/load.js` from the image (`script.localFile`). The commands `cluster k6 e2e`, `cluster k6 smoke` and `cluster k6 load` build and push the image with a new tag before each run. A script change therefore needs no other step.

Use `--scenarios` with a comma-separated list to run a subset, such as `cluster k6 e2e --scenarios cache,access`. An unknown name fails before the run starts. A subset skips the steps in between, so a scenario can fail when it needs state that a skipped scenario leaves.

<!-- nav:start -->
---

[Previous: Thresholds](thresholds.md) | [Next: E2E scenarios](scenarios.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
