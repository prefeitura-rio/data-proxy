{#
{
  "kind": "template",
  "description": "Render the setup policy writer database operation.",
  "inputs": {
    "policy_writer_role": "SQL-safe policy-writer role identifier.",
    "policy_writer_literal": "SQL-safe policy-writer role literal.",
    "authenticator_role": "SQL-safe PostgREST authenticator role identifier.",
    "schema": "PostgreSQL schema that owns the target objects.",
    "policy_name": "SQL-safe access policy identifier."
  }
}
#}
-- noqa: disable=LT02,LT14,LT01,LT05
SELECT format('CREATE ROLE %I NOLOGIN NOBYPASSRLS', {{ policy_writer_literal }})
WHERE NOT EXISTS (
    SELECT 1
    FROM pg_roles
    WHERE rolname = {{ policy_writer_literal }}
)
\gexec
GRANT {{ policy_writer_role }} TO {{ authenticator_role }};
GRANT USAGE ON SCHEMA rls TO {{ policy_writer_role }};
GRANT USAGE ON SCHEMA {{ schema }} TO {{ policy_writer_role }};
GRANT SELECT, INSERT, UPDATE, DELETE ON {{ schema }}.access_policy TO {{ policy_writer_role }};
DROP POLICY IF EXISTS {{ policy_name }} ON {{ schema }}.access_policy;
CREATE POLICY {{ policy_name }} ON {{ schema }}.access_policy
FOR ALL TO {{ policy_writer_role }}
USING (true)
WITH CHECK (true)
