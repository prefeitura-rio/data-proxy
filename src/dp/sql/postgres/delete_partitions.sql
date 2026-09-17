{#
{
  "kind": "template",
  "description": "Render the delete partitions database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "table": "PostgreSQL table being read or changed.",
    "affected_partitions": "SQL-safe partition predicates joined by OR.",
    "claim_setting": "PostgreSQL session setting containing the current claim.",
    "predicate": "SQL predicate applied to the selected rows."
  }
}
#}
DELETE FROM {{ schema }}.{{ table }}
WHERE {{ affected_partitions | join(' OR ') }}
{% if has_rls %}
AND EXISTS (
    SELECT 1 FROM {{ schema }}.access_policy p
    WHERE p.subject = current_setting({{ claim_setting }}, true)
      AND p.is_enabled
      AND (p.is_admin OR {{ predicate }})
)
{% endif %}
