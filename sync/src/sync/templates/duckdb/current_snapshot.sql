{#
{
  "kind": "template",
  "description": "Return the current snapshot ID of the attached DuckLake catalog.",
  "inputs": {}
}
#}
SELECT id
FROM dl.current_snapshot()
