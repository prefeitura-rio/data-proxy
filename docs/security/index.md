# Security

Data Proxy enforces access in PostgreSQL, and no layer above it calculates permissions. A token gives a role and a list of schemas. The database role decides what the caller may do. The access policy decides which rows the caller reads.

## Where to start

| Question | Read |
| --- | --- |
| Which claims must a token have? | [Required JWT claims](claims.md) |
| What can each role do? | [Roles](roles.md) and [Permissions](permissions.md) |
| In which order does the system check a request? | [Schema and row authorization](authorization.md) and [Authorization flow](flow.md) |
| What does a status code mean? | [Requests](requests.md) and [Troubleshooting](troubleshooting.md) |
| How do I set up my identity provider? | [Identity provider](identity.md) |
| How long does a token live, and how do I revoke access? | [Tokens](tokens.md) |

The chart also limits the pods, the network paths and the images. See [Hardening](../operations/hardening.md).

## Configure the access model

These values must agree:

```yaml
auth:
  anonRole: anon
  userRole: dp_api
  servingRole: serving
  jwtRoleClaim: $.role

sync:
  config:
    schemas:
      test:
        claim: preferred_username
        tables:
          - name: project.dataset.participants
            strategy: full
            rls:
              - column: region_id
                unit_type: region
```

- PostgREST reads the PostgreSQL role from the JSON Web Token (JWT) `role` claim.
- The `schemas` claim of the token must contain `test`.
- The database matches the `preferred_username` claim with `access_policy.subject`.
- A row is visible only when its unit columns match an active access policy row.

<!-- nav:start -->
---

[Previous: SQL Templates](../sql.md) | [Next: Required JWT claims](./claims.md) | [Home](../index.md)
<!-- nav:end -->
