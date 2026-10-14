{#
{
  "kind": "template",
  "description": "Store one generated serving-view definition signature.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the view.",
    "view": "PostgreSQL view name.",
    "signature": "SQL-safe signature literal."
  }
}
#}
COMMENT ON VIEW {{ schema }}.{{ view }} IS {{ signature }}
