{#
{
  "kind": "template",
  "description": "Report whether a function signature exists. Parameters: signature."
}
#}
SELECT to_regprocedure(%(signature)s) IS NOT NULL
