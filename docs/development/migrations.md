# Database migrations

The migrations are plain SQL files in `console/migrations/`, and `goose` applies them. `goose` keeps its own version table, `public.goose_db_version`.

They contain the data schema of the console, in the `console` schema. This is the sync configuration tables with their integrity triggers and row policies, and the views that the console reads.

A function belongs to a migration only when a table or a view of the console needs it to exist. The migrations call `rbac.can()` and `rbac.mine()`, which the `configure-db` job creates before `migrate-db` runs. They also call `data_proxy.sees()`, which the same job creates with the state tables.

The migrations contain no grants, because the role of the token is a chart value. The provision job grants the rights on the `console` schema to that role.

The migrations changed before the first release, so an install from an earlier build needs a reset. Drop the old schema and the version table in the application database before the new migrations run: `DROP SCHEMA admin CASCADE; DROP TABLE public.goose_db_version;`. The provision job resets the PostgREST schema list on its own. On k3d, rebuild afterward with `devenv shell -- nu scripts/cluster.nu sync --build`.

Everything else belongs to the chart, in `helm/files/templates/postgres/`:

- the functions and triggers that the console calls (`create_console_*.sql`, which the provision job installs after `goose up`)
- the state, error and scope tables of the engine
- the schemas of the synced data and their access policies
- the pre-request function and its grants
- the rights of the baseline role (`auth.userRole`) on the `console` schema
- the functions that reload PostgREST
- the maintenance procedures
- the DuckLake secrets and the roles

The chart creates the state tables, and DBOS creates its tables, before `goose` runs, because some views read them.

The order is: the chart SQL for the state tables, then `dbos migrate`, then `goose up`. `scripts/db.nu` doesn't run this order for the test database. It creates the databases, loads the first migration (the permission rules that every data schema calls), and installs the reload functions.

Set `PG_DATABASE_URL` and run the `migrate` command in the console shell:

```text
migrate up        # apply every new migration
migrate status    # list the applied migrations
migrate down      # undo the last migration
```

`devenv test` runs `migrate up` on the test database, so a broken migration fails the test setup. `lint:sql` checks the files with `sqlfluff` and `goose validate`.

To add a migration, run `goose -dir console/migrations create <name> sql`. Write both the `Up` and the `Down` part. Never edit a migration that a shared database has applied. Add a new one instead.

<!-- nav:start -->
---

[Previous: Nix setup](nix.md) | [Next: Chart releases](releases.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
