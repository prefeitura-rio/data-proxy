{#
{
  "kind": "template",
  "description": "Grant access to the internal pre-request schema and function, and let the role of the tokens run the function that the console views call to filter the workflows.",
  "inputs": {
    "app_schema": "PostgreSQL schema that holds the pre-request function.",
    "anonymous_role": "Anonymous database role.",
    "user_role": "Application database role."
  }
}
#}
{% from "macros.sql" import grant_schema_usage %}
{{ grant_schema_usage(app_schema, anonymous_role) }}
{{ grant_schema_usage(app_schema, user_role) }}
GRANT EXECUTE ON FUNCTION {{ app_schema }}.pre_request() TO {{ anonymous_role }};
GRANT EXECUTE ON FUNCTION {{ app_schema }}.pre_request() TO {{ user_role }};
GRANT EXECUTE ON FUNCTION {{ app_schema }}.sees(text, text) TO {{ user_role }};
