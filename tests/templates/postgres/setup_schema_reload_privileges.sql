{#
{
  "kind": "template",
  "description": "Create one table that the anonymous role can read.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier.",
    "anonymous_role": "SQL-safe anonymous role identifier."
  }
}
#}
CREATE TABLE {{ schema }}.one (id integer);
GRANT USAGE ON SCHEMA {{ schema }} TO {{ anonymous_role }};
GRANT SELECT ON {{ schema }}.one TO {{ anonymous_role }};
