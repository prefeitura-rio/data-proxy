{#
{
  "kind": "template",
  "description": "List the names of the functions of one schema. Parameters: schema_name.",
  "inputs": {}
}
#}
SELECT p.proname
FROM pg_proc AS p
WHERE p.pronamespace = %(schema_name)s::regnamespace
ORDER BY p.proname
