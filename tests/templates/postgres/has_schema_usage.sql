{#
{
  "kind": "template",
  "description": "Report whether a role has usage on a schema. Parameters: role_name, schema_name."
}
#}
SELECT has_schema_privilege(%(role_name)s, %(schema_name)s, 'USAGE')
