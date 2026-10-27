# Required JWT claims

A normal end-user token for the example above contains claims similar to:

```json
{
  "role": "dp_api",
  "schemas": ["test"],
  "preferred_username": "test_user_1"
}
```

The `role` claim is one role of `rbac.toml`, and PostgREST switches to the database role of the same name. The role decides what the token may do. A token without `read_api` can't read any synced view. The [permissions](permissions.md) section lists all of them.

You configure the exact identity claim per schema. If the schema uses `sub` instead of `preferred_username`, the access policy subject must match the token's `sub` value.

<!-- nav:start -->
---

[Previous: Security](index.md) | [Next: Permissions](permissions.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
