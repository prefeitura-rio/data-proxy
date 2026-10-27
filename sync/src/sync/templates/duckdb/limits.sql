{#
{
  "kind": "template",
  "description": "Limit the memory, the threads and the spill folder of one DuckDB connection, and let DuckDB size a Parquet row group by bytes. Without a limit, a connection takes 80% of the memory of the pod and every thread of the node.",
  "inputs": {
    "memory_limit": "Memory that the connection may use before it spills to disk, as a SQL literal.",
    "threads": "Number of threads of the connection, as a SQL literal.",
    "temp_directory": "Folder where the connection spills to disk, as a SQL literal."
  }
}
#}
SET memory_limit = {{ memory_limit }};
SET threads = {{ threads }};
SET temp_directory = {{ temp_directory }};
SET preserve_insertion_order = false
