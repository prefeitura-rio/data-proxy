{#
{
  "kind": "template",
  "description": "Report whether a relation exists. Parameters: relation."
}
#}
SELECT to_regclass(%(relation)s) IS NOT NULL
