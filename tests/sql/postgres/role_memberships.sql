SELECT member_role.rolname
FROM pg_auth_members
JOIN pg_roles member_role ON member_role.oid = pg_auth_members.member
JOIN pg_roles granted_role ON granted_role.oid = pg_auth_members.roleid
WHERE member_role.rolname = '{{ role }}'
ORDER BY granted_role.rolname
