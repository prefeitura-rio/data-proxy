{#
{
  "kind": "template",
  "description": "Read the sync configuration that the admin tables describe.",
  "inputs": {}
}
#}
SELECT admin.sync_config()::text;
