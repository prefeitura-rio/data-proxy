{#
{
  "kind": "template",
  "description": "Call the procedure access.prune_log with the retention psql variable.",
  "inputs": {}
}
#}
CALL access.prune_log(:'retention'::interval)
