{#
{
  "kind": "template",
  "description": "Render the grant migration access database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "user_role": "Application database role."
  }
}
#}
GRANT USAGE ON SCHEMA {{ schema }} TO {{ user_role }};
GRANT SELECT ON ALL TABLES IN SCHEMA {{ schema }} TO {{ user_role }};
