# Console layer

The console shows the system to people and changes its configuration. It's a web app with a database as its API. The browser never calls PostgREST, and the SvelteKit server calls it with the token of the user.

## Parts

| Part | Work |
| --- | --- |
| Console server | Signs people in, keeps the session, and calls PostgREST. It also signs the file links of the query tab. |
| Notebook site | A static site on its own origin. It runs the notebook of the query tab in the browser. |
| Identity provider | Signs people in and issues the tokens. |
| PostgREST | Serves the `console` schema as an API. |
| `console` schema | The views, the functions and the configuration tables. The database checks every permission. |
| Sync worker | Takes the workflows that the console queues. A worker that listens to the `control` queue runs the control actions. |
| `ducklake` database | The console reads the file lists, and it stores the sealed storage secrets in `signer.keys`. |
| SeaweedFS | Stores the Parquet files. The console creates one storage identity for each schema. |

## Requests

The diagram shows how the console loads a page, starts a sync run and edits the configuration. [Application](../console/app.md) shows the sign-in, and [Query tab](../console/query.md) shows the query tab.

```mermaid
%% Console requests
sequenceDiagram
  participant u as Browser
  participant app as Console server
  participant api as PostgREST
  participant db as PostgreSQL
  participant w as Sync worker
  rect rgba(128, 128, 128, 0.15)
    Note over u,w: Load a page
    u->>app: GET /sync
    app->>api: views, with the user token
    api->>db: switch role, check rights
    db-->>api: rows
    api-->>app: rows
    app-->>u: page
  end
  rect rgba(128, 128, 128, 0.15)
    Note over u,w: Start a run
    u->>app: press Start run
    app->>api: POST /rpc/start_sync
    api->>db: enqueue run_sync
    w->>db: take the run
  end
  rect rgba(128, 128, 128, 0.15)
    Note over u,w: Edit the configuration
    u->>app: save a change
    app->>api: write a row
    api->>db: check schemas, run triggers
    w->>db: take queued workflows
  end
```

See [Console](../console/index.md) for the pages of the app and its database views.

## Run control

A console user can cancel a sync run. The same path serves resume and the schedule actions. The console never changes a workflow itself. The database function checks the permission, the schemas and the status, and then it queues a workflow in the `control` queue. A sync worker runs that workflow with the DBOS API.

```mermaid
%% Run control sequence
sequenceDiagram
  participant u as Console user
  participant app as Console server
  participant api as PostgREST
  participant db as PostgreSQL
  participant w as Sync worker (control queue)
  u->>app: cancel a sync run
  app->>api: POST /rpc/cancel_run
  api->>db: check permission, scope and status
  alt refused
    db-->>api: 403, 404 or 409
  else allowed
    db->>db: enqueue cancel_run in control
    db-->>api: done
  end
  api-->>app: answer
  app-->>u: page
  w->>db: take the workflow
  w->>db: cancel the run and its tasks
```

See [Control a run and a schedule](../console/runs.md) for the allowed statuses and the error codes.

<!-- nav:start -->
---

[Previous: Sync layer](sync.md) | [Next: Sync](../sync.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
