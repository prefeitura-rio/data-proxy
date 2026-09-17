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
-- noqa: disable=LT02,LT05
ALTER TABLE {{ schema }}.{{ table }} ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS access_policy_scoped ON {{ schema }}.{{ table }};
CREATE POLICY access_policy_scoped ON {{ schema }}.{{ table }}
USING (
    {{ scope }}
    AND EXISTS (
        SELECT 1 FROM {{ schema }}.access_policy AS p
        WHERE p.subject = current_setting({{ claim_setting }}, true)
          AND p.is_enabled
          AND (p.is_admin OR (
              {% for mapping in rls_mappings %}
              (p.unit_type = '{{ mapping.unit_type }}' AND p.unit_id = "{{ mapping.column }}"::text){% if not loop.last %} OR {% endif %}
              {% endfor %}
          ))
    )
)
