{#
{
  "kind": "template",
  "description": "Set the DuckLake table sort order, or reset it when no columns are given.",
  "inputs": {
    "table": "SQL-safe DuckLake table identifier.",
    "sort_columns": "Comma-separated SQL-safe sort column identifiers; empty resets."
  }
}
#}
-- noqa: disable=PRS
ALTER TABLE dl.{{ table }}{% if sort_columns %} SET SORTED BY ({{ sort_columns }}){% else %} RESET SORTED BY{% endif %}
