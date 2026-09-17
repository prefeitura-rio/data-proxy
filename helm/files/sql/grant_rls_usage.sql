{#
{
  "kind": "template",
  "description": "Render the grant rls usage database operation.",
  "inputs": {
    "anonymous_role": "Anonymous database role.",
    "user_role": "Application database role."
  }
}
#}
GRANT USAGE ON SCHEMA rls TO {{ anonymous_role }};
GRANT USAGE ON SCHEMA rls TO {{ user_role }};
