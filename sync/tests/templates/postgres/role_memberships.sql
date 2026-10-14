{#
{
  "kind": "template",
  "description": "Select the roles granted to one role. Parameters: role_name."
}
#}
SELECT granted_role.rolname
FROM pg_auth_members
INNER JOIN pg_roles AS member_role ON pg_auth_members.member = member_role.oid
INNER JOIN pg_roles AS granted_role ON pg_auth_members.roleid = granted_role.oid
WHERE member_role.rolname = %(role_name)s
ORDER BY granted_role.rolname
