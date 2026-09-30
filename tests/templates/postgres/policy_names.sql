{#
{
  "kind": "template",
  "description": "Select the RLS policy names of one table. Parameters: schema_name, table_name."
}
#}
SELECT policyname
FROM pg_policies
WHERE schemaname = %(schema_name)s AND tablename = %(table_name)s
