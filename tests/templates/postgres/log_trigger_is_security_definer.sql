{#
{
  "kind": "template",
  "description": "Report whether the access-log trigger function is SECURITY DEFINER. Parameters: schema_name."
}
#}
SELECT prosecdef
FROM pg_proc
WHERE
    proname = 'log_access_policy_change'
    AND pronamespace = %(schema_name)s::regnamespace
