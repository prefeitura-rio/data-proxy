{#
{
  "kind": "template",
  "description": "Set DuckLake table partition keys, or reset them when no expressions are given.",
  "inputs": {
    "table": "SQL-safe DuckLake table identifier.",
    "partitioning": "SQL-safe DuckLake partition expressions; empty resets."
  }
}
#}
-- noqa: disable=PRS
ALTER TABLE dl.{{ table }}{% if partitioning %} SET PARTITIONED BY ({{ partitioning }}){% else %} RESET PARTITIONED BY{% endif %}
