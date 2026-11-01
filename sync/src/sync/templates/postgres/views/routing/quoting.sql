{#
{
  "kind": "template",
  "description": "Quote the name of a column for DuckDB.",
  "inputs": {
    "schema": "PostgreSQL schema that holds the routing functions."
  }
}
#}
-- The name of a column as DuckDB reads it. It is always quoted, because PostgreSQL and
-- DuckDB don't reserve the same words, and a bare name could break in DuckDB.
CREATE OR REPLACE FUNCTION {{ schema }}.quote_column(p_name text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT '"' || replace(p_name, '"', '""') || '"'
$$;
