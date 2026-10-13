{#
{
  "kind": "template",
  "description": "Insert allowed and denied regions and one grant for alice.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier.",
    "user_role": "SQL-safe user role identifier."
  }
}
#}
INSERT INTO {{ schema }}.visible VALUES ('allowed'), ('denied');
INSERT INTO {{ schema }}.access_policy (subject, unit_type, unit_id)
VALUES ('alice', 'region', 'allowed');
GRANT USAGE ON SCHEMA {{ schema }} TO {{ user_role }};
