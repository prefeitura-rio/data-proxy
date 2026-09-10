CREATE OR REPLACE FUNCTION ${schema}.${fn_name}()
RETURNS TABLE(${return_types}) AS $$$$
DECLARE
  v_subject text;
  v_schemas text;
  v_is_admin boolean;
  v_unit_ids text[];
  v_where text;
BEGIN
  v_subject := current_setting('${claim_var}', true);
  v_schemas := current_setting('app.claim_schemas', true);

  IF NOT (${scope}) THEN
    RETURN;
  END IF;

  SELECT EXISTS(
    SELECT 1 FROM ${schema}.access_policy p
    WHERE p.subject = v_subject AND p.is_enabled AND p.is_admin
  ) INTO v_is_admin;

  IF NOT ${has_rls} OR v_is_admin THEN
    v_where := '';
  ELSE
    SELECT string_agg(predicate, ' OR ') INTO v_where
    FROM (
      SELECT format('%I IN (%s)', t.col, string_agg(quote_literal(p.unit_id), ',')) AS predicate
      FROM (VALUES ${unit_values}) AS t(col, ut)
      JOIN ${schema}.access_policy p ON p.unit_type = t.ut
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
    'CREATE OR REPLACE VIEW ${duckdb_view} AS ' ||
    'SELECT ${bq_select_cols} ' ||
    'FROM bigquery_scan(''${bq_table}'') ' || v_where
  );

  RETURN QUERY
    SELECT ${pg_select_cols}
    FROM duckdb.query('SELECT ${duckdb_cols} FROM ${duckdb_view}') r;
END;
$$$$ LANGUAGE plpgsql STABLE SECURITY DEFINER
