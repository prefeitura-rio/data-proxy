{#
{
  "kind": "template",
  "description": "List the wrappers of one schema with the names of their arguments, whether they run as their owner, whether they fix the search path, and whether public and the request role can call them. Parameters: schema_name.",
  "inputs": {}
}
#}
SELECT
    p.proname,
    coalesce(p.proargnames, ARRAY[]::text[]) AS argument_names,
    p.prosecdef AS runs_as_owner,
    coalesce(p.proconfig::text LIKE '%%search_path=%%', false) AS fixed_search_path,
    ARRAY[
        has_function_privilege('public', p.oid, 'EXECUTE'),
        has_function_privilege('dp_api', p.oid, 'EXECUTE')
    ] AS who_can_call
FROM pg_proc AS p
WHERE p.pronamespace = %(schema_name)s::regnamespace
  AND p.proname IN ('agg', 'hll', 'changes')
ORDER BY p.proname
