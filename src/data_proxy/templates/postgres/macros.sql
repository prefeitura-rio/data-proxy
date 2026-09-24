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

{% macro rls_where_clause(schema, has_rls, rls_mappings) -%}
  IF NOT {{ has_rls }} OR v_is_admin THEN
    v_where := '';
  ELSE
    SELECT string_agg(predicate, ' OR ') INTO v_where
    FROM (
      SELECT format('%I IN (%s)', t.col, string_agg(quote_literal(p.unit_id), ',')) AS predicate
      FROM (VALUES
        {{ rls_mapping_values(rls_mappings) }}
      ) AS t(col, ut)
      JOIN {{ schema }}.access_policy p ON p.unit_type = t.ut
      WHERE p.subject = v_subject
      GROUP BY t.col
    ) filters;

    IF v_where IS NULL THEN
      RETURN;
    END IF;

    v_where := 'WHERE ' || v_where;
  END IF;
{%- endmacro %}

{% macro rls_unit_array_checks(rls_mappings, claim_setting, schema) -%}
{% for mapping in rls_mappings %}
"{{ mapping.column }}"::text IN (SELECT unit_id FROM {{ schema }}.access_policy WHERE subject = current_setting({{ claim_setting }}, true) AND unit_type = '{{ mapping.unit_type }}'){% if not loop.last %} OR {% endif %}
{% endfor %}
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
{% for column in columns %}{% if column.is_json %}to_json({{ column.name }}) AS {{ column.name }}{% else %}{{ column.name }}{% endif %}{% if not loop.last %}, {% endif %}{% endfor %}
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
