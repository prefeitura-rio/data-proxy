# Data model

This page shows the tables that Data Proxy owns. Each diagram has every column of its tables. A line joins a column to the column that it references. The crow's foot marks the table that has the reference.

A line is a foreign key or an additional relationship from the `application` section of `docs/.tbls.yaml`. The diagrams show no views and no functions. See [Database Schema](database.md) for those.

`docs:build:model` writes two database diagrams to `docs/diagrams/`. This page includes those files through mdBook, without a second copy of their source. The generator leaves this page unchanged. Run it after a migration. See [Development](dev/index.md).

## Application database

The application database `data-proxy` has four groups of tables.

```mermaid
{{#include diagrams/application.mmd}}
```

- **Configuration (`console`):** `schemas`, `tables` and `rls` store the sync configuration. A table belongs to one schema, and a row policy rule (`rls`) belongs to one table. The name of a schema is `<project>__<schema>`. `config_history` records every change of these three tables. See [Configuration](console/configuration.md) and [Changes](console/changes.md).
- **Engine (`data_proxy`):** `state` has the state of each synced table as JSON. `errors` has the engine errors. `scope` links a workflow to the schemas and tables that a sync run covers. The console views use it to show a user only the sync runs of its own schemas.
- **Rules (`rbac`):** `roles` has the permissions of each role, filled from `rbac.toml`. `revision` is a single row with a counter. The proxy reads the counter to find a change of the rules.
- **Access policy (`access`):** `policy` has the grants of every project: the subjects that may read a unit, in a scope of one `ltree` column. `log` records each change. A trigger writes the log. See [Authorization](security/authorization.md).

The `data_proxy` and `console` tables don't reference each other with foreign keys. `scope.schema_name` and `config_history.schema_name` carry the name of a schema, and nothing enforces it.

## Catalog database

Data Proxy owns `signer.keys` in the `ducklake` database. It stores one storage access key and sealed secret for each schema.

```mermaid
{{#include diagrams/catalog.mmd}}
```

The console reads these keys to sign file links. See [Query tab](console/query.md).

## External dependencies

DBOS owns the workflow tables. Data Proxy starts workflows through `dbos.enqueue_workflow()` and controls them through the DBOS SDK. The owned `data_proxy.scope` table links workflow identifiers to the schemas and tables of a sync run. See [Control a run and a schedule](console/runs.md).

DuckLake owns its catalog metadata. Data Proxy reads selected metadata for file access, sorting and partitioning, but the diagrams omit that internal schema. See [DuckLake storage](database.md#ducklake-storage).
