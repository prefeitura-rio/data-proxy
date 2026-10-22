{#
{
  "kind": "template",
  "description": "Give the role that every token switches to its rights on the console schema: read the views and tables, run the functions, and write the configuration tables. The rights are wide on purpose. A row policy or a check in each function asks for the permission of the token, and the schemas of the token limit the rows.",
  "inputs": {
    "user_role": "Database role that every token switches to."
  }
}
#}
GRANT USAGE ON SCHEMA console TO {{ user_role }};
GRANT SELECT ON ALL TABLES IN SCHEMA console TO {{ user_role }};
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA console TO {{ user_role }};

GRANT INSERT (name, source, settings, claim, encrypted),
UPDATE (source, settings, claim, encrypted)
ON console.schemas TO {{ user_role }};

GRANT INSERT (name, schema, strategy, n, fallback, cache_ttl, partitioning, sort),
UPDATE (strategy, n, fallback, cache_ttl, partitioning, sort)
ON console.tables TO {{ user_role }};

GRANT INSERT (table_name, column_name, unit_type),
UPDATE (column_name, unit_type)
ON console.rls TO {{ user_role }};

GRANT DELETE ON console.schemas, console.tables, console.rls TO {{ user_role }}
