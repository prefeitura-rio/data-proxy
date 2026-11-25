{#
{
  "kind": "template",
  "description": "Create the wrappers agg, hll and changes in one data schema. Each wrapper calls the generic function of the routing schema with the name of the schema, so PostgREST finds it through the schema profile.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL data schema identifier.",
    "schema_literal": "SQL literal of the data schema name.",
    "app_schema": "SQL-safe PostgreSQL schema that holds the generic functions.",
    "user_role": "Role that every token switches to."
  }
}
#}
{#
The argument "table" is quoted because table is a reserved word. A client names it in the query string: ?table=orders.
#}
CREATE OR REPLACE FUNCTION {{ schema }}.agg("table" text) RETURNS json
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, {{ app_schema }}, pg_temp AS $$
  SELECT {{ app_schema }}.agg({{ schema_literal }}, "table")
$$;

CREATE OR REPLACE FUNCTION {{ schema }}.hll(
  "table" text, "column" text, group_by text DEFAULT NULL, lg_k integer DEFAULT 12
) RETURNS json
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, {{ app_schema }}, pg_temp AS $$
  SELECT {{ app_schema }}.hll({{ schema_literal }}, "table", "column", group_by, lg_k)
$$;

CREATE OR REPLACE FUNCTION {{ schema }}.changes(
  "table" text, start_snapshot bigint, end_snapshot bigint DEFAULT NULL
) RETURNS TABLE (
  change_snapshot_id bigint, change_row_id bigint, change_type text, data jsonb
)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, {{ app_schema }}, pg_temp AS $$
  SELECT * FROM {{ app_schema }}.changes({{ schema_literal }}, "table", start_snapshot, end_snapshot)
$$;

REVOKE ALL ON FUNCTION
  {{ schema }}.agg(text),
  {{ schema }}.hll(text, text, text, integer),
  {{ schema }}.changes(text, bigint, bigint)
FROM public;

GRANT EXECUTE ON FUNCTION
  {{ schema }}.agg(text),
  {{ schema }}.hll(text, text, text, integer),
  {{ schema }}.changes(text, bigint, bigint)
TO {{ user_role }}
