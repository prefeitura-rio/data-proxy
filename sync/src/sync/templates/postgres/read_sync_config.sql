{#
{
  "kind": "template",
  "description": "Read the sync configuration that the admin tables describe, or nothing while they are empty.",
  "inputs": {}
}
#}
SELECT admin.sync_config()::text
WHERE EXISTS (SELECT 1 FROM admin.schemas);
