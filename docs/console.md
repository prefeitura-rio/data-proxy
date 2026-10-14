# Console

The console is the admin web app of the data proxy. It lives in `console/`. It's a SvelteKit project with server-side rendering, written in TypeScript and managed with pnpm. Today it has a placeholder page, and the next steps build the pages on top of the database.

## Direction

The database is the API. The `goose` migrations in `console/migrations/` create the tables and the views that the web app needs:

- Views ready to read: the sync runs, the queues and schedules, the configuration revisions, and one `config.json` view with the active configuration for the workers and the proxy.
- Writes through the read/write PostgREST, with the same pattern as `access_policy`: a database role per capability, grants and row-level security.
- The sync worker does what SQL can't: it validates a draft configuration with its own model, and it runs commands such as a sync run.

## Develop

[Development](development.md#console) lists the commands of the `console` profile. The migrations are in [Development](development.md#database-migrations).
