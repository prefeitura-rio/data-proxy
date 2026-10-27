{#
{
  "kind": "template",
  "description": "Check that the roles of the database match the roles of rbac.toml and stop the job with every difference, so that a stale or too powerful role never keeps a right. Each role of the file must exist, must not log in, must not be a superuser, must be in rbac.roles, and the serving login must be a member of it. The serving login must log in without inheriting rights, and must be a member of no role that the file and the anonymous role don't name.",
  "inputs": {
    "roles": "SQL literals, separated by commas, of the roles of rbac.toml.",
    "anonymous_role": "SQL literal of the role of a request without a token.",
    "serving_role": "SQL literal of the login that PostgREST connects with."
  }
}
#}
DO $verify_rbac$
DECLARE
    expected text[] := ARRAY[{{ roles }}];
    request_roles text[] := ARRAY[{{ roles }}, {{ anonymous_role }}];
    problems text[];
BEGIN
    SELECT coalesce(array_agg(problem ORDER BY problem), '{}') INTO problems
    FROM (
        SELECT format('the role %s does not exist', e.role_name) AS problem
        FROM unnest(expected) AS e (role_name)
        WHERE NOT EXISTS (SELECT FROM pg_roles AS r WHERE r.rolname = e.role_name)

        UNION ALL
        SELECT format('%s can log in', rolname)
        FROM pg_roles WHERE rolname = ANY(expected) AND rolcanlogin

        UNION ALL
        SELECT format('%s is a superuser', rolname)
        FROM pg_roles WHERE rolname = ANY(expected) AND rolsuper

        UNION ALL
        SELECT format('%s is not in rbac.roles', e.role_name)
        FROM unnest(expected) AS e (role_name)
        WHERE NOT EXISTS (SELECT FROM rbac.roles AS r WHERE r.name = e.role_name)

        UNION ALL
        SELECT format('%s is in rbac.roles, but not in the file', r.name)
        FROM rbac.roles AS r WHERE r.name <> ALL(expected)

        UNION ALL
        SELECT format('%s is not a member of %s', {{ serving_role }}, e.role_name)
        FROM unnest(request_roles) AS e (role_name)
        WHERE EXISTS (SELECT FROM pg_roles AS r WHERE r.rolname = e.role_name)
            AND NOT EXISTS (
                SELECT FROM pg_auth_members AS m
                INNER JOIN pg_roles AS granted ON granted.oid = m.roleid
                INNER JOIN pg_roles AS member ON member.oid = m.member
                WHERE member.rolname = {{ serving_role }} AND granted.rolname = e.role_name
            )

        UNION ALL
        SELECT format('%s is a member of %s, which the file does not list', {{ serving_role }}, granted.rolname)
        FROM pg_auth_members AS membership
        INNER JOIN pg_roles AS granted ON granted.oid = membership.roleid
        INNER JOIN pg_roles AS member ON member.oid = membership.member
        WHERE member.rolname = {{ serving_role }}
            AND granted.rolname <> ALL(request_roles)

        UNION ALL
        SELECT format('%s inherits the rights of its roles', rolname)
        FROM pg_roles WHERE rolname = {{ serving_role }} AND rolinherit

        UNION ALL
        SELECT format('%s can not log in', rolname)
        FROM pg_roles WHERE rolname = {{ serving_role }} AND NOT rolcanlogin
    ) AS found;

    IF cardinality(problems) > 0 THEN
        RAISE EXCEPTION 'rbac: the roles of the database differ from rbac.toml: %', array_to_string(problems, '; ');
    END IF;
END
$verify_rbac$
