# Console

The console is the admin web app of the data proxy. It lives in `console/`, which has three folders:

- `ui/` is the SvelteKit app.
- `notebooks/` builds the notebook site of the Query tab.
- `migrations/` contains the SQL of the `console` schema.

It's a SvelteKit project with server-side rendering, written in TypeScript and managed with pnpm. It has four tabs: Sync, Config, Access and Query. The browser never calls PostgREST. The SvelteKit server stores the session and calls PostgREST with the token of the user.

- [Application](app.md) — The web app, its lists and its tokens.
- [Database views](views.md) — Database views, logs and the HTTP interface.
- [Control a run and a schedule](runs.md) — Control of sync runs and schedules.
- [Configuration](configuration.md) — Configuration tables, schemas and the configuration endpoint.
- [Changes](changes.md) — Configuration history and import preview.
- [Query tab](query.md) — The query tab and its scope.
- [Query tab scope](scope.md) — The scope of the token of the query tab.

See [Console layer](../architecture/console.md) for the dependencies and the sequence diagrams.

## Direction

The database is the API. The console reads these views, and it writes through the read/write PostgREST with the same pattern as `access_policy`: a database role per capability and grants.

The sync configuration is data in proper tables, and one endpoint serves it to the engine and the proxy. A SQL function starts a sync run with `dbos.enqueue_workflow`, so the console needs no DBOS client.

<!-- nav:start -->
---

[Previous: Troubleshooting](../security/troubleshooting.md) | [Next: Application](app.md) | [Home](../index.md)
<!-- nav:end -->
