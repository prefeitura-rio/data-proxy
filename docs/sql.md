# SQL Templates

Sync templates live under `sync/src/sync/templates/<target>/`. Deployment templates live under `helm/files/templates/postgres/`. This page describes their rendering and input contracts.

## How a request becomes SQL

| Layer | Written in | Runs | Responsibility |
| --- | --- | --- | --- |
| Template | Jinja | At each sync run | Supplies table identifiers and column lists. |
| Static function | PL/pgSQL | At each request | Reads request state and prepares the DuckDB query. |
| Dynamic SQL | `format()` and bound parameters | At each request | Executes the prepared operation with explicit identifier and value semantics. |

The [serving layer](architecture/serving.md) describes the request path. The [sync layer](architecture/sync.md) describes how synchronization installs generated functions.

Keep logic in the layer that owns its inputs and execution lifetime. A table-specific template supplies schema information. A static function can share runtime behavior without generating another copy for every table.

## Template headers

The current template checker reads metadata from a source comment:

```jinja
{#
{
  "kind": "template",
  "description": "Create a table from Parquet.",
  "inputs": {
    "schema": "Destination PostgreSQL schema.",
    "table": "Destination PostgreSQL table.",
    "path": "Parquet file path."
  }
}
#}
```

The `inputs` record describes values that the caller supplies. Local assignments, loop variables and imported macros belong to the template rather than its external inputs. The checker compares external variable use with this record.

Jinja and minijinja use `{# ... #}` comments. Helm Go templates use `{{/* ... */}}` comments. Metadata remains outside rendered SQL.

## Parameters and mappings

Keep the caller's context and the template's variables consistent. Distinguish SQL identifiers, literal values, types and paths because their rendering semantics differ.

Use dedicated quoting for identifiers and bound parameters for data values. Dynamic PL/pgSQL uses `%I` for identifiers, `%L` for SQL literals and `USING` for bound values. Avoid assembling quoting rules through string replacement.

Python prepares sync context mappings. Deployment scripts render a template with one context record and execute it through `execute-sql` in the deployment library. That boundary preserves SQL error handling and the template context.

## Macros and helpers

Reusable Jinja and minijinja macros live in `macros.*` files. Helm helpers live in `helm/templates/_*.tpl`. Keep their inputs and result semantics understandable to callers.

The existing `sql_array` helper renders SQL literal lists, and `name_array` renders plain names. Use the representation that the receiving SQL operation expects.

SQLFluff configuration supplies parsing context for template families. Keep those examples valid for the family they describe. The configured checks and [development commands](dev/index.md#run-the-checks) define current verification.
