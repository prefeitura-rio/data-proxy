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
