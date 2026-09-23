{#
{
  "kind": "macro",
  "name": "postgres.rls_mapping_values",
  "description": "Render RLS unit mappings as SQL VALUES rows.",
  "inputs": {
    "rls_mappings": "Mappings with column and unit_type fields."
  },
  "returns": "Comma-separated SQL VALUES rows."
}
#}
{% macro rls_mapping_values(rls_mappings) -%}
{% for mapping in rls_mappings %}
('{{ mapping.column }}', '{{ mapping.unit_type }}'){% if not loop.last %}, {% endif %}
{% else %}
('', '')
{% endfor %}
{%- endmacro %}

{#
{
  "kind": "macro",
  "name": "postgres.rls_mapping_predicates",
  "description": "Render OR-separated access-policy predicates for RLS mappings.",
  "inputs": {
    "rls_mappings": "Mappings with column and unit_type fields."
  },
  "returns": "An SQL access-policy predicate."
}
#}
{% macro rls_mapping_predicates(rls_mappings) -%}
{% for mapping in rls_mappings %}
(p.unit_type = '{{ mapping.unit_type }}' AND p.unit_id = "{{ mapping.column }}"::text){% if not loop.last %} OR {% endif %}
{% endfor %}
{%- endmacro %}

{#
{
  "kind": "macro",
  "name": "postgres.rls_unit_array_checks",
  "description": "Render per-column IN (SELECT ...) checks that let the planner hash the user's unit_ids once and probe them per row. ANY(ARRAY(SELECT ...)) instead scans the whole array for every row, which costs seconds once a subject holds thousands of units.",
  "inputs": {
    "rls_mappings": "Mappings with column and unit_type fields.",
    "claim_setting": "PostgreSQL session setting containing the current claim.",
    "schema": "PostgreSQL schema that owns access_policy."
  },
  "returns": "An SQL predicate using IN (SELECT ...) per mapping."
}
#}
{% macro rls_unit_array_checks(rls_mappings, claim_setting, schema) -%}
{% for mapping in rls_mappings %}
"{{ mapping.column }}"::text IN (SELECT unit_id FROM {{ schema }}.access_policy WHERE subject = current_setting({{ claim_setting }}, true) AND unit_type = '{{ mapping.unit_type }}'){% if not loop.last %} OR {% endif %}
{% endfor %}
{%- endmacro %}
