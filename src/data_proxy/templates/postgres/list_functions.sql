{#
{
  "kind": "template",
  "description": "List managed functions in the configured schemas.",
  "inputs": {
    "schemas": "PostgreSQL schemas whose functions are listed."
  }
}
#}
SELECT
    routine_schema,
    routine_name
FROM information_schema.routines
WHERE routine_schema = ANY({{ schemas }})
  AND routine_type = 'FUNCTION'
