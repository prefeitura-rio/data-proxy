{#
{
  "kind": "template",
  "description": "Render the table exists database operation."
}
#}
SELECT to_regclass(quote_ident(%s) || '.' || quote_ident(%s)) IS NOT NULL
