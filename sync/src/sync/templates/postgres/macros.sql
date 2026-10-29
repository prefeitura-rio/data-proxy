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
{
  "kind": "macro",
  "name": "postgres.rls_mapping_values",
  "description": "Render RLS unit mappings as SQL VALUES rows.",
  "inputs": {
    "rls_mappings": "Mappings with column and unit_type fields."
  },
  "returns": "Comma-separated SQL VALUES rows."
}
#}
{% macro rls_mapping_values(rls_mappings) -%}
{% for mapping in rls_mappings %}
('{{ mapping.column }}', '{{ mapping.unit_type }}'){% if not loop.last %}, {% endif %}
{% else %}
('', '')
{% endfor %}
{%- endmacro %}

{#
{
  "kind": "macro",
  "name": "postgres.rls_where_clause",
  "description": "Render the PL/pgSQL block that builds the row-level security predicate.",
  "inputs": {
    "schema": "PostgreSQL schema that owns access_policy.",
    "schema_literal": "SQL literal of the schema name, for the all_rows check of the schemas claim.",
    "has_rls": "Enable the row-level security branch.",
    "rls_mappings": "Unit mappings used to build the access-policy predicate.",
    "empty_result": "SQL expression that a function with a scalar result returns when the subject has no grant. Empty for a function that returns rows.",
    "list_form": "Render each column as CAST(col AS VARCHAR) IN (SELECT unnest([...])), which DuckDB runs about four times faster than an IN with thousands of literals. Use it only for a predicate that DuckDB alone reads, because a fallback source cannot push it down."
  },
  "returns": "A PL/pgSQL block assigning v_where."
}
#}
{% macro rls_where_clause(schema, schema_literal, has_rls, rls_mappings, empty_result='', list_form=false) -%}
  -- A token with schema:* for this schema, or with *, or a subject with the grant (*, *), reads every row.
  IF NOT {{ has_rls }} OR rbac.all_rows({{ schema_literal }}) OR EXISTS (
    SELECT 1 FROM {{ schema }}.access_policy p
    WHERE p.subject = v_subject AND p.unit_type = '*' AND p.unit_id = '*'
  ) THEN
    v_where := '';
  ELSE
    SELECT string_agg(predicate, ' OR ') INTO v_where
    FROM (
      SELECT format({% if list_form %}'CAST(%I AS VARCHAR) IN (SELECT unnest([%s]))'{% else %}'%I IN (%s)'{% endif %}, t.col, string_agg(quote_literal(p.unit_id), ',')) AS predicate
      FROM (VALUES
        {{ rls_mapping_values(rls_mappings) }}
      ) AS t(col, ut)
      JOIN {{ schema }}.access_policy p ON p.unit_type = t.ut
      WHERE p.subject = v_subject
      GROUP BY t.col
    ) filters;

    IF v_where IS NULL THEN
      RETURN{% if empty_result %} {{ empty_result }}{% endif %};
    END IF;

    v_where := 'WHERE (' || v_where || ')';
  END IF;
{%- endmacro %}

{#
{
  "kind": "macro",
  "name": "postgres.rls_unit_array_checks",
  "description": "Render per-column IN checks against access_policy for the scoped policy.",
  "inputs": {
    "rls_mappings": "Unit mappings used to build the access-policy predicate.",
    "claim_setting": "PostgreSQL session setting containing the current claim.",
    "schema": "PostgreSQL schema that owns access_policy."
  },
  "returns": "A SQL predicate over access_policy."
}
#}
{% macro rls_unit_array_checks(rls_mappings, claim_setting, schema) -%}
{% for mapping in rls_mappings %}
"{{ mapping.column }}"::text IN (SELECT unit_id FROM {{ schema }}.access_policy WHERE subject = current_setting({{ claim_setting }}, true) AND unit_type = '{{ mapping.unit_type }}'){% if not loop.last %} OR {% endif %}
{% endfor %}
{%- endmacro %}

{#
{
  "kind": "macro",
  "name": "postgres.ducklake_attach",
  "description": "Render the PL/pgSQL block that attaches the read-only DuckLake catalog of one schema, under the alias dl_<schema>. A PostgreSQL backend keeps one DuckDB connection for all of its requests, so each schema needs an alias of its own. With one shared alias, a backend that attached the catalog of one schema would read that catalog for every other schema. The DuckDB secret ducklake_catalog holds the catalog address and credentials. The block first sets the allocator of DuckDB to purge freed memory in the background, with a flush threshold of 16 MiB. A backend that serves for an hour then keeps about 30 MB after a heavy answer, and not 300 MB.",
  "inputs": {
    "metadata_schema": "Schema of the catalog database that holds the DuckLake tables.",
    "data_path": "S3 data path for the DuckLake attachment."
  },
  "returns": "Two PL/pgSQL PERFORM duckdb.raw_query statements."
}
#}
{% macro ducklake_attach(metadata_schema, data_path) -%}
  PERFORM duckdb.raw_query('SET allocator_background_threads = true; SET allocator_flush_threshold = ''16MiB''');
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
{% for column in columns %}{% if column.is_json and not column.raw_json %}to_json({{ column.name }}) AS {{ column.name }}{% else %}{{ column.name }}{% endif %}{% if not loop.last %}, {% endif %}{% endfor %}
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
      r[{{ column.key }}]::{{ 'text' if column.is_json else column.pg_type }} AS {{ column.name }}{% if not loop.last %},{% endif %}
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
      'SELECT {% for column in columns %}{{ column.name }}{% if not loop.last %}, {% endif %}{% endfor %} FROM {{ duckdb_view }}'
    ) r
{%- endmacro %}
