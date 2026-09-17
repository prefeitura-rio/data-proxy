{#
{
  "kind": "template",
  "description": "Render the create bq function database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "function": "PostgreSQL function being created or called.",
    "columns": "Structured SQL-safe column metadata.",
    "claim_setting": "PostgreSQL session setting containing the current claim.",
    "scope": "SQL predicate limiting access to the current schema.",
    "has_rls": "Enable the row-level security branch.",
    "rls_mappings": "Unit mappings used to build the access-policy predicate.",
    "duckdb_view": "DuckDB fallback view name.",
    "bq_table": "BigQuery table reference used by DuckDB."
  }
}
#}
{% from "postgres/macros.sql" import rls_mapping_values %}
CREATE OR REPLACE FUNCTION {{ schema }}.{{ function }}()
RETURNS TABLE(
{% for column in columns %}
{{ column.name }} {{ column.return_type }}{% if not loop.last %}, {% endif %}
{% endfor %}
) AS $$
DECLARE
  v_subject text;
  v_schemas text;
  v_is_admin boolean;
  v_unit_ids text[];
  v_where text;
BEGIN
  v_subject := current_setting('{{ claim_setting }}', true);
  v_schemas := current_setting('app.claim_schemas', true);

  IF NOT ({{ scope }}) THEN
    RETURN;
  END IF;

  SELECT EXISTS(
    SELECT 1 FROM {{ schema }}.access_policy p
    WHERE p.subject = v_subject AND p.is_enabled AND p.is_admin
  ) INTO v_is_admin;

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
      WHERE p.subject = v_subject AND p.is_enabled
      GROUP BY t.col
    ) filters;

    IF v_where IS NULL THEN
      RETURN;
    END IF;

    v_where := 'WHERE ' || v_where;
  END IF;

  PERFORM duckdb.raw_query(
    'LOAD bigquery; ' ||
    'CREATE OR REPLACE VIEW {{ duckdb_view }} AS ' ||
    'SELECT {% for column in columns %}{% if column.is_json %}to_json({{ column.name }}) AS {{ column.name }}{% else %}{{ column.name }}{% endif %}{% if not loop.last %}, {% endif %}{% endfor %} ' ||
    'FROM bigquery_scan(''{{ bq_table }}'') ' || v_where
  );

  RETURN QUERY
    SELECT {% for column in columns %}r[{{ column.key }}]::{{ 'text' if column.is_json else column.pg_type }} AS {{ column.name }}{% if not loop.last %}, {% endif %}{% endfor %}
    FROM duckdb.query('SELECT {% for column in columns %}{{ column.name }}{% if not loop.last %}, {% endif %}{% endfor %} FROM {{ duckdb_view }}') r;
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER
