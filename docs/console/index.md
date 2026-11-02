# Console

The console is the admin web app of Data Proxy. People use it to watch sync runs, change the configuration, manage the access policy and query the data. The browser never calls PostgREST. The SvelteKit server keeps the session and calls PostgREST with the token of the user. The database is the API, and it checks every permission.

| Tab | Use |
| --- | --- |
| Sync | Watch sync runs and tasks, start, cancel or resume a run, and edit a schedule. |
| Config | Edit the schemas, the tables and the rules of the sync configuration. |
| Access | Read and change the access policy. |
| Query | Query the synced data in a notebook in the browser. |

The code is in `console/`. `ui/` is the SvelteKit app, `notebooks/` builds the notebook site of the Query tab, and `migrations/` has the SQL of the `console` schema.

## Pages

- [Application](app.md) — The web app and its sign-in.
  - [Console lists, feedback and tokens](feedback.md) — The lists, the feedback, the live data and the tokens.
- [Theme](theme.md) — The themes and the theme button.
- [Database views](views.md) — Database views, logs and the HTTP interface.
- [Control a run and a schedule](runs.md) — Control of sync runs and schedules.
- [Configuration](configuration.md) — Configuration tables, schemas and the configuration endpoint.
- [Changes](changes.md) — Configuration history and import preview.
- [Query tab](query.md) — The query tab and its scope.
- [Query tab scope](scope.md) — The scope of the token of the query tab.

[Console layer](../architecture/console.md) shows how the parts depend on each other.

<!-- nav:start -->
---

[Previous: Troubleshooting](../security/troubleshooting.md) | [Next: Application](app.md) | [Home](../index.md)
<!-- nav:end -->
