{#
{
  "kind": "template",
  "description": "List PostgreSQL views in the configured schemas.",
  "inputs": {
    "schemas": "PostgreSQL schemas whose views are listed."
  }
}
#}
SELECT
    table_schema,
    table_name
FROM information_schema.views
WHERE table_schema = ANY({{ schemas }})
