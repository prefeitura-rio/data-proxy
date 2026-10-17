{#
{
  "kind": "template",
  "description": "Render a read-only function that lists the Parquet files of one DuckLake table at its latest snapshot.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the function.",
    "function": "PostgreSQL function being created.",
    "metadata_schema": "Schema of the catalog database that holds the DuckLake tables.",
    "data_path": "S3 data path for the DuckLake attachment."
  }
}
#}
-- noqa: disable=CP02,PRS,LT05
{% from "postgres/macros.sql" import ducklake_attach %}
CREATE OR REPLACE FUNCTION {{ schema }}.{{ function }}(p_table text)
RETURNS TABLE (path text, size_bytes bigint, delete_file text) AS $$
BEGIN
{{ ducklake_attach(metadata_schema, data_path) }}

  PERFORM duckdb.raw_query(
    format(
      'CREATE OR REPLACE VIEW dl_known_table AS SELECT table_name FROM duckdb_tables() WHERE database_name = ''dl_{{ metadata_schema }}'' AND table_name = %L',
      p_table
    )
  );

  PERFORM 1 FROM duckdb.query('SELECT table_name FROM dl_known_table') AS known;

  IF NOT FOUND THEN
    RETURN;
  END IF;

  PERFORM duckdb.raw_query(
    format(
      'CREATE OR REPLACE VIEW dl_table_listing AS SELECT data_file, data_file_size_bytes, delete_file FROM ducklake_list_files(''dl_{{ metadata_schema }}'', %L)',
      p_table
    )
  );

  RETURN QUERY
  SELECT r['data_file']::text, r['data_file_size_bytes']::bigint, r['delete_file']::text
  FROM duckdb.query('SELECT data_file, data_file_size_bytes, delete_file FROM dl_table_listing') r;
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, {{ schema }}, pg_temp;

REVOKE ALL ON FUNCTION {{ schema }}.{{ function }}(text) FROM PUBLIC;
