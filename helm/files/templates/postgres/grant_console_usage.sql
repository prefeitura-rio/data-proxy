{#
{
  "kind": "template",
  "description": "Let a console permission level run the pre-request function, which copies the claims of the token, such as its schemas, into session settings.",
  "inputs": {
    "app_schema": "PostgreSQL schema that holds the pre-request function.",
    "console_role": "Console database role that the other levels inherit."
  }
}
#}
{% from "macros.sql" import grant_schema_usage %}
{{ grant_schema_usage(app_schema, console_role) }}
GRANT EXECUTE ON FUNCTION {{ app_schema }}.pre_request() TO {{ console_role }};
