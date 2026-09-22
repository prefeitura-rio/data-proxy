''# SQL Templates

SQL templates live under `src/dp/sql/<target>/`. Each target is one database backend. Helm maintenance SQL lives under `helm/files/sql/helm/`.

## Template headers

Every Jinja or minijinja SQL template starts with one metadata header:

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

Use `kind: template` and `description`. Add `inputs` when the template accepts values. Keep descriptions short. Keep metadata inside the source comment.

Document each Jinja or minijinja macro with one metadata header. Document each Helm `define` in `_helpers.tpl` with the same structure and `kind: macro`.

Put one blank line before a header. Put no blank line between the header and its definition. Put one blank line after the definition. Add `name`, `description`, and `returns`. Add `inputs` when needed.

Use `{# ... #}` for Jinja and minijinja. Use `{{/* ... */}}` for Go templates. Metadata must not appear in rendered output.

## Names

Use one name for each semantic role:

- PostgreSQL objects: `schema`, `table`, `view`, `function`, `policy`, and `index`.
- Columns: `column` for one column and `columns` for a collection.
- Paths: `path` for one path. Use `source` and `target` for two endpoints. Use `scratch_path` for temporary storage.
- Roles: `user_role`, `anonymous_role`, `authenticator_role`, and `policy_writer_role`.
- Predicates: `scope` for a schema predicate, `predicate` for a row or partition predicate, and `claim_setting` for a session-setting name.

Do not merge values with different meanings. Do not use `name` when the object type is known. Do not use shortened aliases such as `cols` or `select_cols`.

## Runtime mappings

Use the same names in Python mappings and template variables. Keep identifier, literal, type, and prepared-parameter semantics in Python helpers. Use Jinja only for presentation loops and conditionals.

Keep SQLFluff context values close to the template family that uses them. Each value must be a valid example for that family.

## Macros and helpers

Use `macros.*` for Jinja and minijinja macro files. Keep Helm helpers in `_helpers.tpl`. Do not generate metadata at runtime. Do not document ordinary functions with macro metadata.
''