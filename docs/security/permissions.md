# Permissions

A token has one `role` claim and a `schemas` claim. The rules live in `helm/files/config/rbac.toml`: it lists the permissions, grouped by object, and the roles with the roles that each one inherits.

The `configure-db` job loads the file into the `rbac` schema. The pre-request function copies the claims into session settings and calls `rbac.load()`, which sets the permissions of the role for the request. `rbac.can('<permission>')` answers whether the role has a permission, and `rbac.mine('<schema>')` answers whether a schema is in the `schemas` claim. Both read settings, so they work inside `SECURITY DEFINER` functions too. A name that the file doesn't list raises an error, so a typo in the code fails loudly.

| Permission | What it opens | How the database checks it |
| --- | --- | --- |
| `read_sync` | The views `runs`, `tasks`, `steps`, `logs`, `errors`, `queues`, `schedules`, `health`, `synced`, and `overview`. | Each view filters on `rbac.can('read_sync')`. |
| `read_config` | The tables `schemas`, `tables`, and `rls`, and `sync_config()`. | The select row policy of each table. |
| `create_config` | Insert on the three tables. | The insert row policies. |
| `edit_config` | Update on the three tables. | The update row policies. |
| `delete_config` | Delete on the three tables. | The delete row policies. |
| `import_config` | `import_config()`. The import also inserts, updates, and deletes rows, so the role needs the other configuration permissions too. | A check at the start of the function. |
| `start_sync` | `start_sync()` and `start_scope()`. | A check at the start of `start_scope()`. |
| `control_sync` | `cancel_run()`, `resume_run()`, `retry_run()`, `pause_schedule()` and `resume_schedule()`. Running a run again also needs `start_sync`. | A check at the start of each function. |
| `read_files` | `table_files()`, the Parquet files for the query tab. | A check at the start of the function. |
| `read_api` | The synced views of the data API. | A check at the start of the function behind each view. |
| `read_ducklake` | The change feed of the catalog. | A check at the start of the function. |
| `read_policy` | Read `<schema>.access_policy`. | The select row policy of each table. |
| `create_policy`, `edit_policy`, `delete_policy` | Insert, update, and delete in `<schema>.access_policy`. | The write row policies of each table. |

A role that edits needs the matching read permission too, because an update reads the row first. The roles in `rbac.toml` already include it.

A role without a permission gets:

- an empty list from a view or a table
- a 403 from a write that a row policy refuses
- a 403 from a function

A delete that the row policy hides removes nothing and answers 200 with an empty list.

Every role sees only the schemas in the `schemas` claim, and a token without the claim sees nothing. No role and no token reaches every schema. The database enforces this, not the console:

- Row-level security filters `schemas`, `tables`, and `rls` by the schema of each row. A role with `create_config` can write a row only for a schema in its claim. A claim can name a schema that doesn't exist yet, so the token can create it.
- The views `tasks`, `logs`, `errors`, `health`, `synced`, and `overview` show the rows of the schemas in the claim. A log line without a schema is hidden.
- When a sync run starts, the engine records the schemas that it covers, and the schema of each dump task, in `data_proxy.scope`. `data_proxy.sees()` tells the views which workflows a token sees. `runs`, `tasks`, and `steps` show a user the sync runs and tasks that cover one of its schemas. The counts of tasks in a sync run count only the tasks that the user sees.
- `GET /rpc/sync_config` returns the schemas of the claim.
- Queues and schedules serve every schema. Any role with `read_sync` reads them. Only a token that covers every schema pauses or resumes a schedule, and no token changes a queue: change a queue in the deployment.

`GET /rpc/permissions` returns the permissions of the role of the token, so the console can show only the controls that work. The pre-request function sets a statement timeout of 30 seconds, and the data API has 15 seconds. `rbac` contains the rules that the data schemas share, and `console` contains what the web app calls: [Database](../database.md#schemas) lists the schemas.

No token can read the DBOS tables or the state tables behind the views.

A token with `start_sync` calls `POST /rpc/start_sync` with the optional arrays `schemas` and `tables`. Empty arrays mean every schema of the token. `console.start_scope(schemas, tables)` shows which schemas a sync run would cover. The function enqueues `run_sync` in the `sync` queue with portable JSON arguments and the name from the token, and the worker takes it from there.

The function refuses some requests. [Request outcomes](#request-outcomes) lists them.

## Request outcomes

This table lists what a client sees and why. A status can come from the gateway, the proxy, PostgREST or the database.

| Status | Cause | Source |
| --- | --- | --- |
| `200 []` | The token has no `schemas` claim, no enabled access policy row matches its subject, or a row policy hides every row. A delete that a row policy hides also returns it. | Database |
| `400` | The claim names an unknown schema, a function gets an unknown action, or the token has an unknown role. | Database or PostgREST |
| `401` | The signature, issuer, audience or expiry of the token isn't valid. | Gateway |
| `403` | The role lacks the permission, a row policy refuses a write, the schema or table is outside the claim (or doesn't exist), or a schedule action needs every schema. | Database |
| `404` | The sync run or the schedule doesn't exist for this token, or the schema profile is unknown. | Database or PostgREST |
| `409` | A sync run is in the wrong status for the action, or the provision job hasn't created the schema yet. | Database |
| `503` | `PROXY_MAX_INFLIGHT` requests already wait for PostgREST. | Proxy |
| `502`, `503` or `504` | PostgREST fails. The proxy retries a `GET` or `HEAD` twice and then returns the last answer. | PostgREST or proxy |

<!-- nav:start -->
---

[Previous: Required JWT claims](claims.md) | [Next: Roles](roles.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
