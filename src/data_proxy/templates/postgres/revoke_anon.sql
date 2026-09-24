{#
{
  "kind": "template",
  "description": "Revoke all privileges and schema usage from the anonymous role.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "anonymous_role": "Anonymous database role."
  }
}
#}
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {{ schema }} FROM {{ anonymous_role }};
REVOKE USAGE ON SCHEMA {{ schema }} FROM {{ anonymous_role }}
