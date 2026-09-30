{#
{
  "kind": "template",
  "description": "Select the existing schemas among a list. Parameters: schema_names."
}
#}
SELECT schema_name
FROM information_schema.schemata
WHERE schema_name = any(%(schema_names)s)
ORDER BY schema_name
