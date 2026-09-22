{#
{
  "kind": "template",
  "description": "Render the swap table database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "old_table": "Old table removed after the replacement.",
    "table": "PostgreSQL table being read or changed.",
    "next_table": "Shadow table that replaces the current table."
  }
}
#}
DROP TABLE IF EXISTS {{ schema }}.{{ old_table }};
ALTER TABLE IF EXISTS {{ schema }}.{{ table }} RENAME TO {{ old_table }};
ALTER TABLE {{ schema }}.{{ next_table }} RENAME TO {{ table }};
DROP TABLE IF EXISTS {{ schema }}.{{ old_table }}
