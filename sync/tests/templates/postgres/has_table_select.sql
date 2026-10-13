{#
{
  "kind": "template",
  "description": "Report whether a role can select a table. Parameters: role_name, table_name."
}
#}
SELECT has_table_privilege(%(role_name)s, %(table_name)s, 'SELECT')
