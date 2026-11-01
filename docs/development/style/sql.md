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
- **SQL-17** Don't edit a migration that another install applied. `console/migrations/SHA256SUMS` has the hash of each file, and `nu scripts/rules.nu sums` rewrites it for a new migration. [must, code]
- **SQL-18** Keep `sync:lint:sql` green. It runs `sqlfluff` on the files with a limit of 88 columns, and `goose validate`. [must, sqlfluff]
- **SQL-19** Give a `SECURITY DEFINER` function a fixed `search_path`, as `data_proxy.sees` does. [must, review]
- **SQL-20** Check access with `rbac.can()` and `rbac.mine()`. Both refuse if the rule is missing. [must, review]

## Tests

- **SQL-21** Put test SQL in `sync/tests/templates/<backend>/`, with the same headers and names as the `src/` templates. Inline only one short statement, such as `SELECT set_config(...)` or `SET ROLE`. [must, review]

## Readable templates

- **SQL-22** Install a function that doesn't depend on the columns of a table once, as a static function in `routing/`. Render only identifiers and column lists in the function of a table. [must, review]
- **SQL-23** Build dynamic SQL in named variables, and call `format()` once for each statement. Don't put `format()` in an argument of `format()` when a variable can store the text. Pass values with `USING`. [should, review]
- **SQL-24** Quote a DuckDB column with `quote_column()`, a PostgreSQL identifier with `%I` and a value with `%L`. Don't quote with `replace`. [must, code]
- **SQL-25** Write a macro that takes arguments and gives text. When a macro reads or sets a variable of the function, name the variable in its header, and let the caller write the `RETURN`. [must, review]
- **SQL-26** Render an array with `sql_array` or `name_array`. Don't write a `for` loop with `loop.last` for a list. [must, review]
- **SQL-27** Name a parameter of a new function `p_<name>` and a local variable `v_<name>`. [should, review]
- **SQL-28** Keep a function under 60 lines. Split a longer function into functions with one job and a name. Four files keep an older long function on a list in `scripts/rules.nu`. [should, code]
- **SQL-29** Keep a template readable by the SQLFluff parser. Don't add `noqa: disable=PRS` to a new template. Add a file to the allowed list in `scripts/rules.nu` only with a reason line under the `noqa`. [must, code]
- **SQL-30** Keep the description of a header under 40 words. Put the detail in a Jinja comment under the header. [must, code]

```sql
✅ v_duckdb_sql := format('SELECT %s FROM view%s', v_plan.select_list, v_plan.group_by);
✅ EXECUTE format('SELECT r[''j'']::text FROM duckdb.query(%L) r', v_duckdb_sql) INTO v_result;
❌ EXECUTE format('SELECT r[''j'']::text FROM duckdb.query(%L) r', format('SELECT %s FROM view%s', a, b)) INTO v_result;
```

## Permissions

Add a permission in these steps:

1. Add it under `[permissions.<object>]` in `helm/files/config/rbac.toml`, and give it to a role.
2. Call `rbac.can('<name>')` in the SQL that needs it.
3. Run `devenv --profile repo tasks run chart:lint:toml`. It checks the file with `taplo` and the JSON schema.
4. Add a role test in `sync/tests/unit/test_templates_roles.py`.
5. Update the permission tables in `docs/security/permissions.md`.

The `configure-db` job runs `verify-rbac` and fails if the database differs from the file.

<!-- nav:start -->
---

[Previous: Nix layout, tasks and documentation](nix-layout.md) | [Next: TypeScript](typescript.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
