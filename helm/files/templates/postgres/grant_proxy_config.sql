{#
{
  "kind": "template",
  "description": "Let the anonymous role read the one document that the proxy needs. The proxy ingress blocks the admin profile, so only the cluster can call it.",
  "inputs": {
    "anonymous_role": "SQL-safe anonymous role identifier."
  }
}
#}
GRANT USAGE ON SCHEMA admin TO {{ anonymous_role }};
GRANT EXECUTE ON FUNCTION admin.config() TO {{ anonymous_role }}
