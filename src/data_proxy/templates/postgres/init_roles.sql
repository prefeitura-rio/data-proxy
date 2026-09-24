{#
{
  "kind": "template",
  "description": "Create the sync_status enum in the RLS schema.",
  "inputs": {
    "rls_schema": "Schema containing row-level security support objects."
  }
}
#}
DO $$
BEGIN
    CREATE TYPE {{ rls_schema }}.sync_status AS ENUM ('success', 'failure');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$
