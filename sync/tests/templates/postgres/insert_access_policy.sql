{#
{
  "kind": "template",
  "description": "Insert one grant into access.policy. Parameters: subject, scope, unit_type, unit_id.",
  "inputs": {}
}
#}
INSERT INTO access.policy (subject, scope, unit_type, unit_id)
VALUES (%(subject)s, %(scope)s::ltree, %(unit_type)s, %(unit_id)s)
