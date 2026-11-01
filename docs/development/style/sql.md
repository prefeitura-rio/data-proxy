# SQL

Applies to: `sync/src/sync/templates/**`, `helm/files/templates/**`, `console/migrations/**`, `helm/files/config/rbac.toml`.

Templates live in `sync/src/sync/templates/<backend>/`. Helm job templates live in `helm/files/templates/postgres/`. See [SQL Templates](../../sql.md).

## Code

- **SQL-01** Build SQL mappings with `Identifier()` and `Literal()`. Use parameters for runtime values. [must, review]
- **SQL-02** Never put a user value into SQL text. [must, review]
- **SQL-03** Use `ON CONFLICT ... DO UPDATE` for upserts. [should, review]
- **SQL-04** Use `timestamptz`, `jsonb` and `bigint GENERATED ALWAYS AS IDENTITY` for a column. A PostgREST contract can need `json`. [must, code]
- **SQL-05** Use one transaction per logical operation, with the `atomic()` helper. [must, review]
- **SQL-06** Use `SECURITY DEFINER` only on purpose, and grant `EXECUTE` to the required role only. [must, review]
- **SQL-07** Mark read-only functions `STABLE`. [should, review]
- **SQL-08** Reload PostgREST with `NOTIFY pgrst, 'reload schema'` through `reload_postgrest()` after the serving definitions change. Don't restart the Deployments. [must, review]
- **SQL-09** Load and delete in sets, not row by row. [must, review]

```python
✅ mapping = {"table": Identifier(schema, name), "limit": Literal(n)}
❌ query = f"SELECT * FROM {schema}.{name} LIMIT {n}"
```

## Backends

- **SQL-10** Let PostgreSQL read Parquet from object storage through `pg_duckdb`. Don't download it first. [must, review]
- **SQL-11** Let DuckDB write one `COPY (...) TO ... (FORMAT PARQUET)` for each dump task. [must, review]
- **SQL-12** Put the behavior of a synchronization source behind a `Source` adapter. Only the BigQuery adapter may use `bigquery_scan` or the BigQuery client. [must, review]
- **SQL-13** Give each `Source` adapter a Pydantic model for the non-secret `source.settings`. Reject unknown, invalid or empty settings when the registry creates the adapter. [must, review]
- **SQL-14** Keep `Source` construction free of side effects. Create external clients on first use and close them through the adapter lifecycle. [must, review]
- **SQL-15** Use a service account or Workload Identity. Don't put credentials in an image. [must, review]

## Console migrations

- **SQL-16** Keep the migrations of the `console` schema in `console/migrations/NNNNN_name.sql`, and let `goose` apply them. Add a new numbered file for each change. [must, goose]
- **SQL-17** Don't edit a migration that another install applied. [must, review]
- **SQL-18** Keep `sync:lint:sql` green. It runs `sqlfluff` on the files with a limit of 88 columns, and `goose validate`. [must, sqlfluff]
- **SQL-19** Give a `SECURITY DEFINER` function a fixed `search_path`, as `data_proxy.sees` does. [must, review]
- **SQL-20** Check access with `rbac.can()` and `rbac.mine()`. Both refuse if the rule is missing. [must, review]

## Tests

- **SQL-21** Put test SQL in `sync/tests/templates/<backend>/`, with the same headers and names as the `src/` templates. Inline only one short statement, such as `SELECT set_config(...)` or `SET ROLE`. [must, review]

## Permissions

Add a permission in these steps:

1. Add it under `[permissions.<object>]` in `helm/files/config/rbac.toml`, and give it to a role.
2. Call `rbac.can('<name>')` in the SQL that needs it.
3. Run `devenv --profile repo tasks run chart:lint:toml`. It checks the file with `taplo` and the JSON schema.
4. Add a role test in `sync/tests/unit/test_templates.py`.
5. Update the permission tables in `docs/security/permissions.md`.

The `configure-db` job runs `verify-rbac` and fails if the database differs from the file.

<!-- nav:start -->
---

[Previous: Nix layout, tasks and documentation](nix-layout.md) | [Next: TypeScript](typescript.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
