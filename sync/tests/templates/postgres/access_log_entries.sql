{#
{
  "kind": "template",
  "description": "Select the rows of access.log in change order.",
  "inputs": {}
}
#}
SELECT
    subject,
    unit_type,
    unit_id,
    action
FROM access.log
ORDER BY changed_at
