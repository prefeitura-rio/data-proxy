# Permissions

A token has one `role` claim and a `schemas` claim. The rules live in `helm/files/config/rbac.toml`: it lists the permissions, grouped by object, and the roles with the roles that each one inherits.

The `configure-db` job loads the file into the `rbac` schema. The pre-request function copies the claims into session settings and calls `rbac.load()`, which sets the permissions of the role for the request. `rbac.can('<permission>')` answers whether the role has a permission, and `rbac.mine('<schema>')` answers whether a schema is in the `schemas` claim. Both read settings, so they work inside `SECURITY DEFINER` functions too. A name that the file doesn't list raises an error, so a typo in the code fails loudly.

| Permission | What it opens |
| --- | --- |
| `read_sync` | The views `runs`, `tasks`, `steps`, `logs`, `errors`, `queues`, `schedules`, `health`, `synced`, and `overview`. |
| `read_config` | The tables `schemas`, `tables`, and `rls`, and `sync_config()`. |
| `create_config` | Insert on the three tables. |
| `edit_config` | Update on the three tables. |
| `delete_config` | Delete on the three tables. |
| `import_config` | `import_config()`. The import also inserts, updates, and deletes rows, so the role needs the other configuration permissions too. |
| `start_sync` | `start_sync()` and `start_scope()`. |
| `control_sync` | `cancel_run()`, `resume_run()`, `retry_run()`, `pause_schedule()`, `resume_schedule()` and `set_schedule()`. Running a run again also needs `start_sync`. |
| `read_files` | `table_files()`, the Parquet files for the query tab. |
| `read_api` | The synced views of the data API. |
| `read_ducklake` | The change feed of the catalog. |
| `read_policy` | Read `<schema>.access_policy`. |
| `create_policy`, `edit_policy`, `delete_policy` | Insert, update, and delete in `<schema>.access_policy`. |

A role that edits needs the matching read permission, because an update reads the row first. The roles in `rbac.toml` include it.

The database checks a permission with a row policy or with a check at the start of the function. A role without a permission gets:

- an empty list from a view or a table
- a 403 from a write that a row policy refuses
- a 403 from a function

A delete that the row policy hides removes nothing and answers 200 with an empty list.

Every role sees only the schemas in the `schemas` claim and in the `projects` claim, and a token without them sees nothing. No role and no token reaches every schema. The `projects` claim names projects. When a request starts, `rbac.load()` expands each entry into the schemas that the project has: `<schema>` for `<project>`, and `<schema>:*` for `<project>:*`. A token with a project can add a schema in it, even though the schema doesn't exist yet. The wildcard `*` names every project. The database enforces this, not the console:

- Row-level security filters `schemas`, `tables` and `rls` by the schema of each row. A role with `create_config` writes a row only for a schema in its claim, and the claim can name a schema that doesn't exist yet.
- The views `tasks`, `logs`, `errors`, `health`, `synced` and `overview` show the rows of the schemas in the claim. A log line without a schema is hidden.
- When a sync run starts, the engine records the schemas that it covers, and the schema of each dump task, in `data_proxy.scope`. `data_proxy.sees()` tells the views which workflows a token sees. The views `runs`, `tasks` and `steps` then show only the sync runs and tasks that cover a schema of the user.
- `GET /rpc/sync_config` returns the schemas of the claim.
- Queues serve every schema. Any role with `read_sync` reads them, and no token changes a queue. A schedule belongs to one schema, and a token controls the schedules of the schemas in its claim.

`GET /rpc/permissions` returns the permissions of the role of the token, so the console can show only the controls that work. The pre-request function sets a statement timeout of 30 seconds, and the data API has 15 seconds. [Database](../database.md#schemas) lists the schemas.

A token with `start_sync` calls `POST /rpc/start_sync` with the optional arrays `schemas` and `tables`. Empty arrays mean every schema of the token. The function enqueues `run_sync` in the `sync` queue, and a worker takes it from there.

## Request outcomes

This table lists what a client sees and why. A status can come from the gateway, the proxy, PostgREST or the database.

| Status | Cause | Source |
| --- | --- | --- |
| `200 []` | The token has no `schemas` claim, no enabled access policy row matches its subject, or a row policy hides every row. A delete that a row policy hides also returns it. | Database |
| `400` | The claim names an unknown schema, a function gets an unknown action, or the token has an unknown role. | Database or PostgREST |
| `401` | The signature, issuer, audience or expiry of the token isn't valid. | Gateway |
| `403` | The role lacks the permission, a row policy refuses a write, or the schema or table is outside the claim (or doesn't exist). | Database |
| `404` | The sync run or the schedule doesn't exist for this token, or the schema profile is unknown. | Database or PostgREST |
| `409` | A sync run is in the wrong status for the action, or the provision job hasn't created the schema yet. | Database |
| `503` | `PROXY_MAX_INFLIGHT` requests already wait for PostgREST. | Proxy |
| `502`, `503` or `504` | PostgREST fails. The proxy retries a `GET` or `HEAD` twice and then returns the last answer. | PostgREST or proxy |

<!-- nav:start -->
---

[Previous: Required JWT claims](./claims.md) | [Next: Roles](./roles.md) | [Index](./index.md) | [Home](../index.md)
<!-- nav:end -->
