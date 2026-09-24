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
  "name": "postgres.rls_mapping_predicates",
  "description": "Render OR-separated access-policy predicates for RLS mappings.",
  "inputs": {
    "rls_mappings": "Mappings with column and unit_type fields."
  },
  "returns": "An SQL access-policy predicate."
}
#}
{% macro rls_mapping_predicates(rls_mappings) -%}
{% for mapping in rls_mappings %}
(p.unit_type = '{{ mapping.unit_type }}' AND p.unit_id = "{{ mapping.column }}"::text){% if not loop.last %} OR {% endif %}
{% endfor %}
{%- endmacro %}

{#
{
  "kind": "macro",
  "name": "postgres.rls_unit_array_checks",
  "description": "Render per-column IN (SELECT ...) checks that let the planner hash the user's unit_ids once and probe them per row. ANY(ARRAY(SELECT ...)) instead scans the whole array for every row, which costs seconds once a subject holds thousands of units.",
  "inputs": {
    "rls_mappings": "Mappings with column and unit_type fields.",
    "claim_setting": "PostgreSQL session setting containing the current claim.",
    "schema": "PostgreSQL schema that owns access_policy."
  },
  "returns": "An SQL predicate using IN (SELECT ...) per mapping."
}
#}
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
