{#
{
  "kind": "template",
  "description": "Create rows that one region grant or one group grant for alice allows.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier.",
    "user_role": "SQL-safe user role identifier."
  }
}
#}
CREATE TABLE {{ schema }}.multi_visible (region_id text, group_id text);
INSERT INTO {{ schema }}.multi_visible VALUES
('region_allowed', 'other'),
('other', 'group_allowed'),
('denied', 'denied');
INSERT INTO {{ schema }}.access_policy (subject, unit_type, unit_id) VALUES
('alice', 'region', 'region_allowed'),
('alice', 'group', 'group_allowed');
GRANT USAGE ON SCHEMA {{ schema }} TO {{ user_role }};
