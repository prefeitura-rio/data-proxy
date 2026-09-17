{#
{
  "kind": "template",
  "description": "Render the create index database operation.",
  "inputs": {
    "name": "Index name.",
    "schema": "PostgreSQL schema that owns the target objects.",
    "table": "PostgreSQL table being read or changed.",
    "method": "Optional PostgreSQL index method clause.",
    "columns": "Structured SQL-safe column metadata."
  }
}
#}
CREATE INDEX IF NOT EXISTS {{ name }}
ON {{ schema }}.{{ table }}{{ method }} ({{ columns | join(', ') }})
