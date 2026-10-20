# Console

The console is the admin web app of the data proxy. It lives in `console/`, which has three folders: `ui/` is the SvelteKit app, `notebooks/` builds the notebook site of the Query tab, and `migrations/` holds the SQL of the `console` schema. It's a SvelteKit project with server-side rendering, written in TypeScript and managed with pnpm. It has four tabs: Sync, Config, Access and Query. The browser never calls PostgREST. The SvelteKit server holds the session and calls PostgREST with the token of the user.

## The web app

The app signs in people with OpenID Connect: the authorization code flow with PKCE and a `state` value. The server keeps the access token and the refresh token in an encrypted cookie. The cookie is `httpOnly`, `SameSite=Lax` and host-only. The token is too long for one cookie, so the server splits the sealed value into pieces of 3500 characters. The server refreshes the token 60 seconds before it expires, and a failed refresh sends the user back to the sign-in page. Every request that changes data must come from the origin of the console, or the server answers 403.

`GET /rpc/permissions` decides which tabs and buttons the user sees, and the database still checks every call. The routes are `/sync`, `/sync/runs/<id>`, `/config`, `/access` and `/query`. The code of the app is in `console/ui/src`: `lib/server/` holds the session, the OpenID Connect client, the PostgREST client, the catalog reader and the signer, and `lib/types.ts` holds the types of the rows. The types are written by hand. Update them in the same commit that changes a view.

The Config page edits a row in a dialog. A schema has a `claim` and `encrypted`, a table has a strategy, `n`, `fallback`, a cache TTL and sort columns, and a row rule has a column and a unit type. The page doesn't edit `partitioning`. Change it with an import of the document. The Access page lists the access policy of one schema and its access log, which the same `read_policy` permission opens. A run is visible to the user who started it from the first second, because `console.start_sync` records the schemas of the run in the same transaction that queues it.

The route `/healthz` answers 200 without a session and calls nothing else. The chart uses it for the probes of the Deployment, and it sets resource requests and limits from `console.resources` and `notebooks.resources`.

| Setting | Holds |
| --- | --- |
| `OIDC_ISSUER` | The address of the identity provider. |
| `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | A confidential client with the redirect URI `<origin>/auth/callback`. |
| `COOKIE_SECRET` | The secret that seals the cookie. |
| `POSTGREST_URL` | The address of PostgREST. |
| `ORIGIN` | The public origin of the console. |
| `PROTOCOL_HEADER` | The header that gives the scheme to the Node adapter, `x-forwarded-proto` behind the gateway. Without it, the adapter assumes `https` and refuses every form post from an `http` site. |
| `NOTEBOOK_ORIGIN`, `CATALOG_URL`, `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | The query tab. Without them, the tab says that it isn't set up. `S3_REGION` is optional. |
| `S3_ADMIN_ACCESS_KEY`, `S3_ADMIN_SECRET_KEY`, `S3_IAM_ENDPOINT` | Optional. They turn on one storage identity for each schema. The IAM endpoint is the storage endpoint by default. |

The chart sets these settings ([Helm chart](helm_chart.md#console-and-notebooks)). In the k3d cluster, the realm `dev` has the client `console-web` with the secret `test-secret`, and the users `web_viewer` (`dp_console`), `web_admin` (`dp_admin`) and `web_other` (`dp_admin` for the schema `other`). Their password is `web-test`. The client `console` of the realm is another client: the end-to-end suite uses it with client credentials.

## Views

The `console` schema holds the views that the console reads through PostgREST. `console/migrations/00002_create_console_views.sql` creates them, and the `console` scenario of the end-to-end suite (`k6/scenarios/console.js`) tests them.

| View | Content |
| --- | --- |
| `runs` | Sync runs: status, queue, schedule, who started it, the times, and the number of tasks and failed tasks. |
| `tasks` | The dump and publish workflows of a run. |
| `steps` | The steps of a run and its tasks, with the times and a `failed` flag. |
| `logs` | The log lines of a run and its tasks, read from the portable DBOS log stream. |
| `queues` | The queue settings and the number of enqueued, pending, and delayed workflows. |
| `schedules` | The schedules with their cron expression and status. |
| `errors` | The errors that the sync recorded, newest first. |
| `overview` | One row for each schema: its tables, how many have committed state, its last run, and the runs, failed runs, and errors of the last 24 hours. The dashboard reads it in one request. |

## Configuration tables

The sync configuration is data in three tables of the `console` schema. `console/migrations/00004_create_config.sql` creates them, and the end-to-end suite tests the rules.

| Table | Holds |
| --- | --- |
| `schemas` | One row per synced schema: `name`, `source`, `settings`, `claim`, `encrypted`. |
| `tables` | One row per source table: `name`, its `schema`, `strategy`, `n`, `fallback`, `cache_ttl`, `partitioning`, `sort`. |
| `rls` | One row per rule: the `table_name`, the `column_name`, and the `unit_type`. |

Every row records `updated_at` and `updated_by`. The database sets both. The name comes from the `preferred_username` claim of the token, then from `sub`.

Constraints replace a validation step:

- A schema name uses lowercase letters, digits and underscores, and it starts with a letter.
- A table name is unique in the whole configuration, and unique in its schema by its last part.
- `n` and `fallback` need the `partitioned` strategy, and `n` must be positive.
- A table needs a schema that exists, and an `rls` rule needs a schema with a `claim`.
- A schema can't lose its `claim` while one of its tables has a rule.

The engine still checks the rules that belong to a source, such as the form of a table name, when a run starts.

Two read-only views compare the configuration with the system. `health` shows, for each schema, whether the PostgreSQL schema, its `access_policy`, and its views exist. `synced` shows, for each table, whether a sync committed its state, with the signature and the number of partitions.

## Remove a table

A trigger on `tables` queues the workflow `cleanup_tables` when a delete happens and none waits. The workflow runs in the sync worker, because dropping a DuckLake table needs DuckDB. It reads the configuration and, for each schema, drops the DuckLake tables that the configuration no longer lists. Then it deletes the data files of each dropped table in S3, and the state and the errors of every table that the configuration no longer lists. The daily `maintenance` Job drops the views. Every step can run again. A run that stops between the drop and the file deletion leaves files behind, and the orphan cleanup of the DuckLake maintenance deletes them later.

Deleting a row in `tables` therefore destroys the data of the table. The database asks for no confirmation: the console asks the user before it deletes.

## Add and remove a schema

A trigger on `schemas` queues the workflow `provision_schemas` when none waits (the queue runs one at a time, so a change during a run gets the next run), and the sync worker creates a Job from the suspended `provision` CronJob ([Helm chart](helm_chart.md#run-a-job-on-demand)). The Job reads the configuration and makes the database match it. Every sync run does the same at its start, which heals a lost event.

- **Add:** the Job creates the PostgreSQL schema with `access_policy` and `access_log`, adds the schema to `pgrst.db_schemas`, and tells PostgREST to reload. It takes seconds.
- **Remove:** the Job stops serving the schema first. Then it purges the data files in S3, drops the metadata schema in the catalog database, deletes the state, the errors and the scope of the tables, and drops the schema of the database. The schema of the database goes last, because the next run finds an unfinished removal through it. Every step can run again.

Deleting a row in `schemas` therefore destroys the data of the schema, and `delete_config` is a destructive permission. The database asks for no confirmation: the console asks the user before it deletes. A run for a schema that an editor removes during the run fails.

## The configuration endpoint

`GET /rpc/sync_config` returns the whole configuration in the shape of `sync.json`. A token with `read_config` gets the schemas of its claim. The engine and the proxy read this one document. `console.sync_config()` builds it from the tables, and it leaves out every default value: no `claim` without a claim, no `rls` without rules, and no `fallback` unless it's true. The tables come out sorted by name, and the rules keep their order.

A new setup can load an existing file. A token with `import_config`, `edit_config`, and `delete_config` calls `POST /rpc/import_config` with `{"document": <sync.json>}`. The function inserts or updates the schemas and tables of the document, and it replaces the rules of each table in the document. It never deletes a schema or a table, and it never moves a table to another schema. The call is one transaction, so a row the constraints refuse leaves nothing behind. `sync/tests/integration/test_console_sync_config.py` proves that a document comes back unchanged after an import and an export, and that the engine accepts the result.

One limit: the export drops a `null` value inside the `settings` of a source, because the export removes every `null` of the document.

The proxy reads a smaller document, `console.config()`: the table names and their `cache_ttl`. The chart grants it to the anonymous role, and the proxy refuses the `console` profile from outside, so only the cluster can call it. A trigger on `schemas`, `tables`, and `rls` queues a `reload_proxy` workflow after every statement. A change queues a reload only when none waits, so a burst of edits shares one reload, a change during a run queues the next one, and an editor never sees an error from it. The sync worker then sends a reload request to every proxy pod ([Proxy](proxy.md#table-settings-from-the-configuration)).

## Permissions

A token carries one `role` claim and a `schemas` claim. The rules live in `helm/files/config/rbac.toml`: it lists the permissions, grouped by object, and the roles with the roles that each one inherits. The `configure-db` job loads the file into the `rbac` schema. The pre-request function copies the claims into session settings and calls `rbac.load()`, which sets the permissions of the role for the request. `rbac.can('<permission>')` answers whether the role holds a permission, and `rbac.mine('<schema>')` answers whether a schema is in the `schemas` claim. Both read settings, so they work inside `SECURITY DEFINER` functions too. A name that the file doesn't list raises an error, so a typo in the code fails loudly.

| Permission | What it opens | How the database checks it |
| --- | --- | --- |
| `read_sync` | The views `runs`, `tasks`, `steps`, `logs`, `errors`, `queues`, `schedules`, `health`, `synced`, and `overview`. | Each view filters on `rbac.can('read_sync')`. |
| `read_config` | The tables `schemas`, `tables`, and `rls`, and `sync_config()`. | The select policy of each table. |
| `create_config` | Insert on the three tables. | The insert policies. |
| `edit_config` | Update on the three tables. | The update policies. |
| `delete_config` | Delete on the three tables. | The delete policies. |
| `import_config` | `import_config()`. The import also inserts, updates, and deletes rows, so the role needs the other configuration permissions too. | A check at the start of the function. |
| `start_sync` | `start_sync()` and `start_scope()`. | A check at the start of `start_scope()`. |
| `read_files` | `table_files()`, the Parquet files for the query tab. | A check at the start of the function. |
| `read_api` | The synced views of the data API. | A check at the start of the function behind each view. |
| `read_ducklake` | The change feed of the catalog. | A check at the start of the function. |
| `read_policy` | Read `<schema>.access_policy`. | The select policy of each table. |
| `create_policy`, `edit_policy`, `delete_policy` | Insert, update, and delete in `<schema>.access_policy`. | The write policies of each table. |

A role that edits needs the matching read permission too, because an update reads the row first. The roles in `rbac.toml` already include it.

A role without a permission gets an empty list from a view or a table, a 403 from a write that a row policy refuses, and a 403 from a function. A delete that the policy hides removes nothing and answers 200 with an empty list.

Every role sees only the schemas in the `schemas` claim, and a token without the claim sees nothing. No role and no token reaches every schema. The database enforces this, not the console:

- Row-level security filters `schemas`, `tables`, and `rls` by the schema of each row. A role with `create_config` can write a row only for a schema in its claim. A claim can name a schema that doesn't exist yet, so the token can create it.
- The views `tasks`, `logs`, `errors`, `health`, `synced`, and `overview` show the rows of the schemas in the claim. A log line without a schema is hidden.
- When a run starts, the engine records the schemas that it covers, and the schema of each dump task, in `data_proxy.scope`. `data_proxy.sees()` tells the views which workflows a token sees. `runs`, `tasks`, and `steps` show a user the runs and tasks that cover one of its schemas. The counts of tasks in a run count only the tasks that the user sees.
- `GET /rpc/sync_config` returns the schemas of the claim.
- Queues and schedules serve every schema. Any role with `read_sync` reads them, and no token changes them: change them in the deployment.

`GET /rpc/permissions` returns the permissions of the role of the token, so the console can show only the controls that work. The pre-request function gives a request to the `console` profile a statement timeout of 30 seconds, and the data API has 15 seconds. `rbac` holds the rules that the data schemas share, and `console` holds what the web app calls: [Database](database.md#schemas) lists the schemas.

No token can read the DBOS tables or the state tables behind the views. A token with `start_sync` calls `POST /rpc/start_sync` with the optional arrays `schemas` and `tables`. Empty arrays mean every schema of the token. `console.start_scope(schemas, tables)` shows which schemas a run would cover. A schema or a table outside the claim gets a 403, and so does a table that doesn't exist, so a name tells nothing. An unknown schema in the claim gets a 400 error. A schema that the provision job hasn't created yet, with its access policy, gets a 409 with the reason. A schema that a token adds is ready within a minute or two. The function enqueues `run_sync` in the `sync` queue with portable JSON arguments and the name from the token, and the worker takes it from there.

## Roles

`rbac.toml` defines four roles, and each inherits the permissions of the roles that it lists. The identity provider and the database use the same names. A token carries one role.

| Role | Inherits | Adds |
| --- | --- | --- |
| `dp_api` | | `read_api` |
| `dp_policy` | | `read_policy`, `create_policy`, `edit_policy`, `delete_policy` |
| `dp_console` | `dp_api` | `read_sync`, `read_config` |
| `dp_admin` | `dp_console`, `dp_policy` | `start_sync`, `create_config`, `edit_config`, `delete_config`, `import_config`, `read_files`, `read_ducklake` |

`dp_policy` is for an API that registers its own access policies. `dp_admin` runs schemas, and every role is limited to the schemas in its claim. A user holds one role. A person who needs both console and policy rights takes `dp_admin`.

## How the rules are tested

The Python tests in `sync/tests/unit/test_templates.py` run the `create_rbac` template on the real test server with the real `rbac.toml`. They compare `rbac.can()` for every role and permission with a reference that the test computes itself, and Hypothesis tests do the same for random rules files and random names. The end-to-end suite in `k6/scenarios/` tests the console with real tokens (`console.js` and `roles.js`) on the k3d cluster. It doesn't run in CI, because it needs the cluster: run it before you merge a change to a permission, a role, a policy, a view, or the console schema (`devenv shell -- nu scripts/cluster.nu k6 e2e`).

## Tokens from the identity provider

The console sends the access token of the user to PostgREST. PostgREST checks it against the keys of the identity provider, like it does for the data API. The token needs these claims besides the standard ones:

| Claim | Holds |
| --- | --- |
| `role` | One role of `rbac.toml`, such as `dp_console`. It's the database role that PostgREST switches to. |
| `schemas` | The list of schemas that the user manages, such as `["sales", "hr"]`. |

The `preferred_username` claim names the user in `updated_by` and `started_by`.

## Reading logs and runs

The console polls. A page reads `GET /logs?run_id=eq.<id>&logged_at=gte.<last>&order=logged_at.asc` every few seconds, and it drops the lines that it already has, using the pair of `workflow_id` and `position`. It reads `GET /runs?id=eq.<id>` and `GET /tasks?run_id=eq.<id>` for the status. `GET /overview` gives the dashboard in one request.

## The query tab

A role with `read_files` queries the synced data in a notebook in the browser. The notebook is a marimo export that runs Python (Pyodide) and DuckDB compiled to WebAssembly. The files stay in object storage, and the browser reads them directly. The notebook runs on its own origin, `notebooks.<domain>`, which is a different site from the console, and the console page embeds it in an `iframe`. The notebook has no token and no cookie of the console.

The grant that the console server builds for one schema has four steps:

1. **Authorize.** The server calls `GET /rpc/table_files?schema_name=<schema>&table_name=<source table>` with the token of the user. `console.table_files` needs `read_files`. It checks the schemas of the token, and it answers 403 for a schema or a table outside them, for a name that doesn't exist, and for a token without the permission. It answers 409 for a schema with encrypted files, because the browser has no key. The engine generates `dl_table_files` in every schema, and the function calls it.
2. **List.** The server connects to the catalog database with the `console_signer` login and runs `SET LOCAL ROLE schema_<name>` for the schema that step 1 authorized. That role reads the metadata of its schema only. The server reads the data files and the delete files of each table at the latest snapshot.
3. **Sign.** The server signs a `GET` link for each listed path, valid for 5 minutes. PostgREST can't sign links, so the database never holds storage credentials. The credential is the identity of that schema: a user in the storage with a policy that allows `s3:GetObject` on the folder of the schema and nothing else. The console makes the identity on the first query of the schema, through the IAM API of the storage, with an admin credential (`S3_ADMIN_ACCESS_KEY`, `S3_ADMIN_SECRET_KEY`). The storage shows the secret of a key only once, so the console seals it and stores it in `signer.keys` in the catalog database. An advisory lock keeps two replicas from making two keys. If the console can't open a stored key, it makes a new one. Without the admin credential, every schema signs with one read-only credential.
4. **Send.** The page sends `{ schema, tables, expiresAt }` to the notebook with `postMessage` and the exact origin of the notebook. It sends a new grant every 4 minutes. The notebook builds one view for each table. A data file with a delete file becomes an anti join that removes the deleted rows, so the counts match the database.

`provision` creates the role `schema_<name>` with the schema, and drops it with the schema. The role has no login. It has `USAGE` on the metadata schema of its schema and `SELECT` on its tables, now and for the tables that DuckLake creates later. `configure-db` creates the `console_signer` login without inheritance, and its password is in the Secret `<release>-signer`. It also creates the table `signer.keys` in the catalog database, where only that login reads and writes. The drop of a schema deletes its row. The sync worker removes the identity in the storage: the step `cleanup_s3_credentials` runs in the workflows `provision_schemas` and `cleanup_tables`, lists the users named `sig_<schema>`, and deletes the keys, the policies and the user of each one whose schema the configuration no longer lists. The step runs only when `S3_IDENTITIES` is `true`, which the chart sets with `console.storage.perSchemaIdentities`. It uses the admin credential that the worker already has for DuckLake. The login is a member of every `schema_<name>` role and must switch into one. A schema name has at most 56 characters, so that the role name fits in 63.

The files hold every row, and row-level security doesn't apply to them. That's the reason why only roles with `read_files` can list them. [Security](security.md#query-tab-scope) describes what each layer stops and what the layers don't stop.

The bucket needs a CORS rule for the origin of the notebook, or the browser can't read the files. The rule allows `GET` and `HEAD`, allows the `Range` header, and exposes `Content-Length`, `Content-Range`, `ETag`, and `Accept-Ranges`. Without `Content-Range`, DuckDB reads a wrong size and fails with the message No magic bytes found. Set `s3.bootstrap.corsOrigins` to the notebook origins, and the bootstrap Job sets the rule through the S3 API of SeaweedFS or S3. The Job needs the permission to change the bucket, and it skips a GCS endpoint. GCS needs its own command.

The notebook origin sends a Content Security Policy that allows only its own files and the storage host. [Notebooks](notebooks.md) describes the build of the site.

## HTTP interface

The console talks to PostgREST only. The `console` scenario in `k6/scenarios/console.js` tests this contract against the cluster.

- Send `Authorization: Bearer <JWT>` with the `role` claim of the user (one role of `rbac.toml`) and the schemas of the user in the `schemas` claim. Send `Accept-Profile: console` on reads and `Content-Profile: console` on writes, because PostgREST also serves the schemas of the synced data. The access tab sends the data schema as the profile.
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

## Direction

The database is the API. The console reads these views, and it writes through the read/write PostgREST with the same pattern as `access_policy`: a database role per capability and grants. The sync configuration is data in proper tables, and one endpoint serves it to the engine and the proxy. A SQL function starts a run with `dbos.enqueue_workflow`, so the console needs no DBOS client.

## Develop

[Development](development.md#console) lists the commands of the `console` profile and of the tests. The migrations are in [Development](development.md#database-migrations).
