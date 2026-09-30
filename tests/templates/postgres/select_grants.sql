{#
{
  "kind": "template",
  "description": "Select the roles that can select one table. Parameters: schema_name, table_name, role_name."
}
#}
SELECT grantee
FROM information_schema.role_table_grants
WHERE
    table_schema = %(schema_name)s
    AND table_name = %(table_name)s
    AND privilege_type = 'SELECT'
    AND grantee = %(role_name)s
