# Configuration

The console edits the sync configuration in the database. These sections cover the tables, the removal of a table or a schema, and the configuration endpoint.

## Configuration tables

The sync configuration is data in three tables of the `console` schema. `console/migrations/00003_create_config.sql` creates them, and the end-to-end suite tests the rules.

| Table | Contains |
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

The engine still checks the rules that belong to a source, such as the form of a table name, when a sync run starts.

Two read-only views compare the configuration with the system. `health` shows, for each schema, whether the PostgreSQL schema, its `access_policy`, and its views exist. `synced` shows, for each table, whether a sync committed its state, with the signature and the number of partitions.

## Remove a table

A trigger on `tables` queues the workflow `cleanup_tables` when a delete happens and none waits. The workflow runs in the sync worker, because dropping a DuckLake table needs DuckDB.

It reads the configuration. For each schema, it drops the DuckLake tables that the configuration no longer lists. Then it deletes the data files of each dropped table in S3. It also deletes the state and the errors of every table that the configuration no longer lists.

The daily `maintenance` Job drops the views. Every step can run again. A run that stops between the drop and the file deletion leaves files behind. The orphan cleanup of the DuckLake maintenance deletes them later.

Deleting a row in `tables` therefore destroys the data of the table. The database asks for no confirmation: the console asks the user before it deletes.

## Add and remove a schema

A change to the schemas of the configuration queues a workflow, and a Job makes the database match the configuration.

A trigger on `schemas` queues the workflow `provision_schemas` when none waits. The queue runs one at a time, so a change during a sync run gets the next sync run. The sync worker creates a Job from the suspended `provision` CronJob ([Helm chart](../operations/initialization.md#run-a-job-on-demand)). The Job reads the configuration and makes the database match it. Every sync run does the same at its start, which heals a lost event.

- **Add:** the Job creates the PostgreSQL schema with `access_policy` and `access_log`, adds the schema to `pgrst.db_schemas`, and tells PostgREST to reload. It takes seconds.
- **Remove:** the Job stops serving the schema first. Then it does these steps:
  - It purges the data files in S3.
  - It drops the metadata schema in the catalog database.
  - It deletes the state, the errors and the scope of the tables.
  - It drops the schema of the database. This step goes last, because the next sync run finds an unfinished removal through it.

  Every step can run again.

Deleting a row in `schemas` therefore destroys the data of the schema, and `delete_config` is a destructive permission. The database asks for no confirmation: the console asks the user before it deletes. A run for a schema that an editor removes during the sync run fails.

## The configuration endpoint

`GET /rpc/sync_config` returns the whole configuration in the shape of `sync.json`. A token with `read_config` gets the schemas of its claim. The engine and the proxy read this one document.

`console.sync_config()` builds it from the tables. It omits every default value. There is no `claim` without a claim, no `rls` without rules, and no `fallback` unless it's true. The tables are sorted by name, and the rules keep their order.

A new setup can load an existing file. A token with `import_config`, `edit_config`, and `delete_config` calls `POST /rpc/import_config` with `{"document": <sync.json>}`. The function inserts or updates the schemas and tables of the document, and it replaces the rules of each table in the document. It never deletes a schema or a table, and it never moves a table to another schema. The call is one transaction, so a row the constraints refuse leaves nothing behind. The scenario "The exported document imports again" in `console/ui/tests/features/config.feature` exports the document, previews it with no change, and applies it.

One limit: the export drops a `null` value inside the `settings` of a source, because the export removes every `null` of the document.

The proxy reads a smaller document, `console.config()`: the table names and their `cache_ttl`. The chart grants it to the anonymous role, and the proxy refuses the `console` profile from outside, so only the cluster can call it.

A trigger on `schemas`, `tables`, and `rls` queues a `reload_proxy` workflow after every statement. A change queues a reload only when none waits. A burst of edits therefore shares one reload. A change during a sync run queues the next one, and an editor never sees an error from it. The sync worker then sends a reload request to every proxy pod ([Table settings](../proxy/reload.md)).

<!-- nav:start -->
---

[Previous: Control a run and a schedule](runs.md) | [Next: Changes](changes.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
