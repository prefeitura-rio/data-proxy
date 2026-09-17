{#
{
  "kind": "template",
  "description": "Render the schema scope statement database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "table": "PostgreSQL table being read or changed.",
    "scope": "SQL predicate limiting access to the current schema."
  }
}
#}
ALTER TABLE {{ schema }}.{{ table }} ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS schema_scoped ON {{ schema }}.{{ table }};
CREATE POLICY schema_scoped ON {{ schema }}.{{ table }}
USING (
    {{ scope }}
)
