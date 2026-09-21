{#
{
  "kind": "template",
  "description": "Render the access policy check database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "table": "PostgreSQL table being read or changed.",
    "scope": "SQL predicate limiting access to the current schema.",
    "claim_setting": "PostgreSQL session setting containing the current claim.",
    "mapping": "Structured mapping used by the template."
  }
}
#}
{% from "postgres/macros.sql" import rls_unit_array_checks %}
-- noqa: disable=LT02,LT05
ALTER TABLE {{ schema }}.{{ table }} ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS access_policy_scoped ON {{ schema }}.{{ table }};
CREATE POLICY access_policy_scoped ON {{ schema }}.{{ table }}
USING (
    (SELECT {{ scope }})
    AND (
        (SELECT EXISTS(SELECT 1 FROM {{ schema }}.access_policy WHERE subject = current_setting({{ claim_setting }}, true) AND is_enabled AND is_admin))
        OR {{ rls_unit_array_checks(rls_mappings, claim_setting, schema) }}
    )
)
