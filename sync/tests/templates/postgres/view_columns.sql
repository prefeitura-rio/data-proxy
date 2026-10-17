{#
{
  "kind": "template",
  "description": "Select the columns of one view in order. Parameters: schema_name, table_name."
}
#}
SELECT column_name
FROM information_schema.columns
WHERE table_schema = %(schema_name)s AND table_name = %(table_name)s
ORDER BY ordinal_position
