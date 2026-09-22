{#
{
  "kind": "template",
  "description": "Persist one structured error event in dp.errors.",
  "inputs": {
    "schema": "PostgreSQL schema that holds application state (default dp)."
  }
}
#}
INSERT INTO {{ schema }}.errors (reason, fields)
VALUES (%(reason)s, %(fields)s::jsonb);
