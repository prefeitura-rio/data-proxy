SELECT prosecdef
FROM pg_proc
WHERE proname = 'log_access_policy_change'
  AND pronamespace = '{{ schema }}'::regnamespace
