{#
{
  "kind": "template",
  "description": "Create render_filter_node as an older release made it, with the parameters node and columns.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
{#
CREATE OR REPLACE can't rename a parameter, so the routing templates must replace this function.
The old function gives a fixed text, so a test sees whether the new function replaced it.
#}
CREATE FUNCTION {{ schema }}.render_filter_node(node jsonb, columns text[]) RETURNS text
LANGUAGE sql IMMUTABLE AS $$ SELECT 'old'::text $$
