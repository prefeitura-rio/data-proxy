{#
{
  "kind": "template",
  "description": "Delete rows from a DuckLake table, all rows when no predicate is given.",
  "inputs": {
    "table": "SQL-safe DuckLake table identifier.",
    "predicate": "SQL-safe partition predicate; empty deletes every row."
  }
}
#}
DELETE FROM dl.{{ table }}
{% if predicate %}WHERE {{ predicate }}
{% endif %}
