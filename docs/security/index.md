# Security

Data Proxy enforces access in PostgreSQL. The table function passes the row-level security (RLS) predicate into DuckDB, so the Parquet scan reads only authorized rows.

PostgreSQL applies PostgREST query filters, such as `?id=eq.1`, after DuckDB returns the authorized rows. Data Proxy doesn't calculate business permissions in nginx or in the client.

- [Required JWT claims](claims.md) — The claims that a token must carry.
- [Permissions](permissions.md) — Permissions, roles and the schemas claim.
- [Roles](roles.md) — The four roles and how the rules are tested.
- [Authorization flow](flow.md) — How a token becomes a database role and a row filter.
- [Schema and row authorization](authorization.md) — Schema conditions, row conditions and the access policy.
- [Requests](requests.md) — Read requests and their results.
- [Identity provider](identity.md) — Identity provider setup and requirements.
- [Tokens](tokens.md) — Token lifetime and revoking access.
- [Troubleshooting](troubleshooting.md) — Common failures and their causes.

## Configure the access model

The following values must agree:

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

These settings mean:

- PostgREST reads the PostgreSQL role from the JSON Web Token (JWT) `role` claim.
- `schemas` must contain `test`.
- The database matches the JWT `preferred_username` value with `access_policy.subject`.
- Rows are visible only when the unit columns match enabled access policy rows.

<!-- nav:start -->
---

[Previous: SQL Templates](../sql.md) | [Next: Required JWT claims](claims.md) | [Home](../index.md)
<!-- nav:end -->
