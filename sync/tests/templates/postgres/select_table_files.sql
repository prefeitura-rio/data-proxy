{#
{
  "kind": "template",
  "description": "List the data files of one table through the generated file function of a schema.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the generated function."
  }
}
#}
SELECT path, size_bytes, delete_file FROM {{ schema }}.dl_table_files(%(table_name)s);
