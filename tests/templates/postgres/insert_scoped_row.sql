{#
{
  "kind": "template",
  "description": "Insert one visible row into the scoped table.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
INSERT INTO {{ schema }}.scoped VALUES ('visible')
