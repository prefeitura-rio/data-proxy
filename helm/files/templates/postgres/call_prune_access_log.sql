{#
{
  "kind": "template",
  "description": "Call the prune_access_log procedure with retention and schema psql variables.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
CALL {{ schema }}.prune_access_log(:retention::interval, :schema)
