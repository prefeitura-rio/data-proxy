{#
{
  "kind": "template",
  "description": "Drop one managed PostgreSQL function.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the function.",
    "function": "Function to remove.",
    "arguments": "Static PostgreSQL function argument signature."
  }
}
#}
DROP FUNCTION IF EXISTS {{ schema }}.{{ function }}{{ arguments }};
