# Console API

The console is a FastAPI service for operators. It shows the sync runs, follows them live, and edits the sync configuration. It talks to two databases: the DBOS system database, through the DBOS client, and the application database, through `SQLAlchemy`.

Run it with `granian --interface asgi --loop uvloop console.main:app`. [Environment Variables](environment_variables.md) lists its settings. The OpenAPI document is at `/openapi.json`.

## Sync configuration

The configuration is one JSON document. The database keeps every version as a revision, and exactly one revision is active. The document has the shape of `sync.json`, with one change: the `tables` of a schema are a map by table name, and `name` is called `source_table`.

```json
{
  "schemas": {
    "bigquery_a": {
      "source": { "type": "bigquery" },
      "claim": "preferred_username",
      "ducklake": { "encrypted": false },
      "tables": {
        "people": { "source_table": "project.dataset.people", "strategy": "full" }
      }
    }
  }
}
```

The key of a table must be the last part of its `source_table`. The database stores the document in the `sync.json` shape, so the engine can read it without a conversion. `GET /config/sync.json` serves that stored document untouched. Its `ETag` is the id of the active revision, and a consumer that sends the `ETag` it holds in `If-None-Match` gets 304 while nothing changed. The engine loads it once at the start of each run, from `SYNC_CONFIG_URL`.

| Request                                | What it does                                                                                                 |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `GET /config`                          | Shows the active configuration and its revision.                                                             |
| `PUT /config`                          | Replaces the whole configuration.                                                                            |
| `PATCH /config`                        | Changes part of it with a JSON Merge Patch: objects merge, `null` removes a key, and anything else replaces. |
| `GET /config/sync.json`                | Serves the active configuration exactly as `sync.json`, for the engine and the proxy.                        |
| `GET /config/revisions`                | Lists the revisions, newest first, without their documents.                                                  |
| `GET /config/revisions/{id}`           | Shows one revision with its document.                                                                        |
| `POST /config/revisions/{id}/activate` | Rolls back by making an older revision active. The history stays.                                            |

Every write needs `If-Match` with the `ETag` that the caller read. The `ETag` is the id of the active revision, and `"0"` before the first write. A write over a newer revision gets 412, and a write without the header gets 428. Send `X-Forwarded-User` to record who wrote the revision, and `?comment=` to say why.

Add `?dry_run=true` to `PUT` or `PATCH` to check a configuration without writing it. The answer lists the problems and the parts that would change:

```json
{
  "valid": true,
  "errors": [],
  "changes": [{ "pointer": "/schemas/bigquery_a/tables/orders", "change": "added" }]
}
```

After each write, patch, or activation, the console calls `POST /reload` on every proxy that `PROXY_ADMIN_URL` resolves to, so the proxies use the new settings at once. The call runs after the console has answered. A proxy that the console can't reach doesn't fail the write: it catches up at its next periodic check. See [Proxy](proxy.md#table-settings-from-the-console). A dry run calls nothing.

The engine needs no reload, because each run loads the active configuration again at its start.

A configuration that isn't valid gets 422 with `application/problem+json`. Each error has a JSON Pointer to the part that causes it. The engine's own `SyncConfig` model does the validation, so the console accepts exactly what the engine accepts.

## Schemas and tables

A client that manages one schema or one table, such as a Terraform provider, can use the resources of the configuration. They write through the same revisions, so the history, the rollback and the `GET /config/sync.json` document stay the same.

| Request                                          | What it does                                            |
| ------------------------------------------------ | ------------------------------------------------------- |
| `GET /schemas`                                   | Lists the schemas, each with the names of its tables.   |
| `GET`, `PUT`, `PATCH`, `DELETE /schemas/{s}`     | Reads, creates or replaces, merges, and removes a schema. |
| `GET /schemas/{s}/tables`                        | Shows the tables of a schema as a map.                  |
| `GET`, `PUT`, `PATCH`, `DELETE /schemas/{s}/tables/{t}` | The same four operations for one table.          |

Each schema and each table has its own `ETag`, a hash of its content. A read returns it. A write needs one of two headers:

- `If-Match` with the `ETag` that the caller read, to replace, patch or delete. A resource that changed since gets 412.
- `If-None-Match: *` to create. A resource that exists already gets 412.

A write without either header gets 428. The `ETag` is per resource, so two callers can change two tables of one schema without a conflict. A write answers with the resource, its new `ETag`, and the id of the new revision in `X-Revision`. A delete answers 204.

The table name in the path must be the last part of `source_table`. Add `?dry_run=true` to any write to get `{valid, errors, changes}` and no revision, and `?comment=` to record why. A 422 has the same JSON Pointers as `PUT /config`, because the console validates the whole configuration after each edit.

`GET /schemas/{s}/status` reports which objects of a schema exist. It only reads. Each check has a name, a result and a reason, and the schema is `ready` when all pass:

| Check             | Passes when                                                                |
| ----------------- | -------------------------------------------------------------------------- |
| `postgres_schema` | The PostgreSQL schema exists.                                              |
| `access_policy`   | `access_policy` and `access_log` exist in it.                              |
| `policy_writer`   | The role `policy_writer_<schema>` exists and the PostgREST role is a member. |
| `views`           | Every configured table has a view. The reason lists the ones that miss.    |
| `catalog`         | The catalog database has a metadata schema for it. Only with `DUCKLAKE_CATALOG_URL`. |

A delete removes the schema or the table from the configuration only. The synced data stays in DuckLake, and the next sync run drops the PostgreSQL views of what you removed.

## Monitoring streams

These endpoints send server-sent events. Each one sends the current state at once, and then again each time it changes. A browser can use `EventSource`.

| Stream                     | Event                                                                  | Ends                                     |
| -------------------------- | ---------------------------------------------------------------------- | ---------------------------------------- |
| `GET /queues/stream`       | `queues`: the same data as `GET /queues`, with the depth of each queue | Never. The client disconnects.           |
| `GET /runs/{id}/stream`    | `run`: the status, the visible steps and the child workflows by status | With the run, after an `end` event.      |
| `GET /config/stream`       | `revision`: the active revision                                        | Never.                                   |
| `GET /workflows/{id}/logs` | `log`: one log line of the workflow                                    | With the workflow, after an `end` event. |

The server polls every `STREAM_INTERVAL_SECONDS` and sends an event only when the data differs from the last one. The log stream uses the offset of a line as the event id, so a client that reconnects with `Last-Event-ID` continues at the next line.

## Runs, queues and schedules

`GET /runs` and `GET /runs/{id}` show the sync runs and their steps. The steps that DBOS records for itself are hidden unless the request has `?include_internal=true`. `GET /queues` and `GET /schedules` show the queue limits, the queue depths and the cron schedules.

## Queues and schedules

`PATCH /queues/{name}` changes the limits of a queue with a JSON Merge Patch: `global_concurrency`, `worker_concurrency` and `rate_limit` (`{"limit": 100, "period_seconds": 60}`). A field that the body leaves out stays, and null removes a limit. DBOS refuses limits that contradict each other, for example a worker limit above the global limit, and the answer is 422. The partition limits and the polling interval stay as they are.

`PATCH /schedules/{name}` changes `cron`, `timezone` or `status` (`ACTIVE` or `PAUSED`) the same way. A new cron or time zone replaces the schedule, which resets its last fire time. A paused schedule stays paused. A cron that DBOS can't parse gets 422 with the pointer `/cron`.

The engine creates its queues and its schedule only when they don't exist, so a restart or a new worker keeps what the console set. A write has no `ETag`: the last write wins.

Every 422, also for a body that doesn't match the model, is `application/problem+json` with a JSON Pointer to the field.

## Starting a run

`POST /syncs` starts a sync run now and answers `202` with `{"workflow_id": "…"}` and a `Location` header for `GET /runs/{id}`. The run waits on the sync queue, so it never overlaps another run.

The body is optional. Without it, the run plans every table, like the nightly run. With a body, it plans only part of the configuration:

```json
{ "schemas": ["bigquery_a"], "tables": ["bigquery_b.orders"] }
```

A schema in `schemas` picks all its tables. A table in `tables` is written as `schema.table`. Both lists can name different parts at once. A name that the active configuration doesn't know gets 422 with a JSON Pointer to it, and nothing starts.

A scoped run still reconciles the views of whole schemas, so a table outside the scope keeps its view.

## Tests

The API tests call the app over HTTP. The tests that use the application database carry the `postgres` marker. `test:sync` skips them, and `devenv test` runs them against the test database.
