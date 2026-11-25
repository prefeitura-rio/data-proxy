{#
{
  "kind": "macro",
  "name": "postgres.require_permission",
  "description": "Render the PL/pgSQL check that refuses a token without one permission.",
  "inputs": {
    "permission": "Name of a permission of rbac.toml."
  },
  "returns": "A PL/pgSQL block that raises insufficient_privilege."
}
#}
{% macro require_permission(permission) -%}
  IF NOT rbac.can('{{ permission }}') THEN
    RAISE EXCEPTION 'The token lacks the {{ permission }} permission'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
{%- endmacro %}

{#
{
  "kind": "macro",
  "name": "postgres.require_scope",
  "description": "Render the PL/pgSQL check that returns no row for a schema outside the schemas claim of the token.",
  "inputs": {
    "schema_literal": "SQL literal of the schema name.",
    "empty_result": "SQL expression that a function with a scalar result returns. Empty for a function that returns rows."
  },
  "returns": "A PL/pgSQL block that leaves the function with an empty result."
}
#}
{% macro require_scope(schema_literal, empty_result='') -%}
  IF NOT rbac.mine({{ schema_literal }}) THEN
    RETURN{% if empty_result %} {{ empty_result }}{% endif %};
  END IF;
{%- endmacro %}

{#
A PostgreSQL backend keeps one DuckDB connection for all of its requests, so each schema needs an alias of its own.
With one shared alias, a backend that attached the catalog of one schema would read that catalog for every other schema.
The DuckDB secret ducklake_catalog holds the catalog address and credentials.
The block first sets the allocator of DuckDB to purge freed memory in the background, with a flush threshold of 16 MiB.
A backend that serves for an hour then keeps about 30 MB after a heavy answer, and not 300 MB.
#}

{#
{
  "kind": "macro",
  "name": "postgres.ducklake_attach",
  "description": "Render the PL/pgSQL block that attaches the read-only DuckLake catalog of one schema under the alias dl_<schema>, after it sets the allocator of DuckDB.",
  "inputs": {
    "metadata_schema": "Schema of the catalog database that holds the DuckLake tables.",
    "data_path": "S3 data path for the DuckLake attachment."
  },
  "returns": "Two PL/pgSQL PERFORM duckdb.raw_query statements."
}
#}
{% macro ducklake_attach(metadata_schema, data_path) -%}
  PERFORM duckdb.raw_query(
    'SET allocator_background_threads = true; SET allocator_flush_threshold = ''16MiB'''
  );
  PERFORM duckdb.raw_query(
    'ATTACH IF NOT EXISTS ''ducklake:ducklake_catalog'' AS "dl_{{ metadata_schema }}" '
    || '(DATA_PATH ''{{ data_path }}'', METADATA_SCHEMA ''{{ metadata_schema }}'', READ_ONLY)'
  );
{%- endmacro %}

{#
{
  "kind": "macro",
  "name": "postgres.column_projection",
  "description": "Render a DuckDB SELECT projection that converts nested and JSON columns to JSON.",
  "inputs": {
    "columns": "Structured SQL-safe column metadata."
  },
  "returns": "A comma-separated DuckDB projection expression."
}
#}
{% macro column_projection(columns) -%}
{% for column in columns -%}
  {%- if column.is_json and not column.raw_json -%}
    to_json({{ column.name }}) AS {{ column.name }}
  {%- else -%}
    {{ column.name }}
  {%- endif -%}
  {{ ', ' if not loop.last }}
{%- endfor %}
{%- endmacro %}

{#
{
  "kind": "macro",
  "name": "postgres.return_query_select",
  "description": "Render the RETURN QUERY SELECT block that reads typed columns from a duckdb.query result row.",
  "inputs": {
    "columns": "Structured SQL-safe column metadata."
  },
  "returns": "A PL/pgSQL RETURN QUERY SELECT statement."
}
#}
{% macro return_query_select(columns) -%}
    SELECT
{% for column in columns %}
      r[{{ column.key }}]::{{ 'text' if column.is_json else column.pg_type }}
        AS {{ column.name }}{% if not loop.last %},{% endif %}
{% endfor %}
{%- endmacro %}

{#
{
  "kind": "macro",
  "name": "postgres.duckdb_query_select",
  "description": "Render a duckdb.query(...) call selecting all column names from a view.",
  "inputs": {
    "columns": "Structured SQL-safe column metadata.",
    "duckdb_view": "DuckDB view name."
  },
  "returns": "A PL/pgSQL duckdb.query(...) call."
}
#}
{% macro duckdb_query_select(columns, duckdb_view) -%}
    FROM duckdb.query(
      'SELECT {% for column in columns %}{% if column.is_json %}CAST(to_json({{ column.name }}) AS VARCHAR) AS {{ column.name }}{% else %}{{ column.name }}{% endif %}{% if not loop.last %}, {% endif %}{% endfor %} FROM {{ duckdb_view }}'
    ) r
{%- endmacro %}

{#
{
  "kind": "macro",
  "name": "postgres.sql_array",
  "description": "Render a PostgreSQL text array from SQL literals.",
  "inputs": {
    "literals": "SQL literals, such as the key of a column."
  },
  "returns": "An ARRAY[...]::text[] expression. An empty list gives an empty array."
}
#}
{% macro sql_array(literals) -%}
ARRAY[{{ literals | join(', ') }}]::text[]
{%- endmacro %}

{#
{
  "kind": "macro",
  "name": "postgres.name_array",
  "description": "Render a PostgreSQL text array from plain names, and put each name in quotes.",
  "inputs": {
    "names": "Names that need no escape, such as a type name or the name of a fallback source."
  },
  "returns": "An ARRAY[...]::text[] expression. An empty list gives an empty array."
}
#}
{% macro name_array(names) -%}
ARRAY[{% for name in names %}'{{ name }}'{% if not loop.last %}, {% endif %}{% endfor %}]::text[]
{%- endmacro %}
