# Schema and row authorization

Authorization has two independent conditions.

## Schema condition

The token must list the requested schema:

```json
"schemas": ["test"]
```

The client must select the same schema:

```http
Accept-Profile: test
```

## Row condition

For a table with this configuration:

```json
"rls": [
  {"column": "region_id", "unit_type": "region"}
]
```

an active access policy row must match the user's subject and the row's unit:

```text
subject   = JWT preferred_username (or configured claim)
OR unit_type/unit_id matches the row
```

An access policy row exists only while the grant is active. Revoking access deletes the row. A missing schema claim or access policy grant normally returns `200 []`. It isn't an authentication error.

## Seed access policy

Create one confidential client for the service that manages the access policy, and give its token the role `dp_policy` for the schemas that it manages. Its token looks like this:

```json
{
  "role": "dp_policy",
  "schemas": ["test"],
  "preferred_username": "test_policy"
}
```

The token reads and writes `<schema>.access_policy` for the schemas in its claim. `dp_policy` has only the four policy permissions: `read_policy`, `create_policy`, `edit_policy`, and `delete_policy`. Deleting a row revokes the grant. The database records the change in `test.access_log`.

<!-- nav:start -->
---

[Previous: Authorization flow](flow.md) | [Next: Requests](requests.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
