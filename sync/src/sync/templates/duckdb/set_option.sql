{#
{
  "kind": "template",
  "description": "Set one persistent DuckLake option.",
  "inputs": {
    "option": "DuckLake option name.",
    "value": "SQL literal option value."
  }
}
#}
-- noqa: disable=PRS
CALL dl.set_option('{{ option }}', {{ value }})
