{#
{
  "kind": "template",
  "description": "Read the sync configuration that the console tables describe.",
  "inputs": {}
}
#}
SELECT console.sync_config()::text;
