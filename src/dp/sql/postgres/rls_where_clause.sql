{#
{
  "kind": "template",
  "description": "Render the rls where clause database operation.",
  "inputs": {
    "scope": "SQL predicate limiting access to the current schema.",
    "schema": "PostgreSQL schema that owns the target objects.",
    "claim_setting": "PostgreSQL session setting containing the current claim.",
    "mapping": "Structured mapping used by the template."
  }
}
#}
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
