# Terraform resources

Every resource of the provider needs `provider = data-proxy`, because a provider local name has no underscore. The [provider page](terraform.md) explains why. A refused write names the permission that it needed, such as `create_config`.

## `data_proxy_project`

A project groups schemas. The project `default` exists already, so the resource refuses that name.

```hcl
resource "data_proxy_project" "sales" {
  provider = data-proxy
  name     = "sales"
}
```

- `name`: a lowercase letter and up to 29 lowercase letters and digits. A change replaces the project.
- `updated_at` and `updated_by`: filled by the database.

The database refuses to delete a project that has schemas, so Terraform deletes the schemas first. Import with the name: `terraform import data_proxy_project.sales sales`.

## `data_proxy_schema`

A schema has the tables of one source. The sync creates its PostgreSQL schema after the row appears.

```hcl
resource "data_proxy_schema" "orders" {
  provider            = data-proxy
  project             = data_proxy_project.sales.name
  short_name          = "orders"
  claim               = "preferred_username"
  deletion_protection = false
}
```

| Attribute | Content |
| --- | --- |
| `project` | The project. The default is `default`. A change replaces the schema. |
| `short_name` | The name inside the project. A change replaces the schema. |
| `name` | The physical name: the short name in `default`, else `<project>_<short_name>`. Tables and access policies use it. |
| `source`, `settings`, `claim`, `encrypted` | The columns of the schema. `settings` is a JSON object, such as `jsonencode({...})`. |
| `deletion_protection` | Refuses the delete while it's `true`, which is the default. Only Terraform reads it. |

A create waits until the sync provisions the schema, for 5 minutes at most. A delete waits until the sync drops it, for 10 minutes. Set `timeouts { create = "0s" }` or `delete = "0s"` to skip the wait. A replace or a delete drops the data, so the plan shows it.

Import with the physical name. An imported schema has deletion protection.

## `data_proxy_table`

A table that the sync copies from the source into a schema.

```hcl
resource "data_proxy_table" "orders" {
  provider = data-proxy
  name     = "sales.dataset.orders"
  schema   = data_proxy_schema.orders.name
  sort     = ["order_id"]

  rls {
    column_name = "unit_id"
    unit_type   = "unit"
  }
}
```

- `name` and `schema`: a change replaces the table and syncs it again.
- `strategy`: `full` (default) or `partitioned`. `n` and `fallback` need `partitioned`.
- `cache_ttl`: the seconds that the proxy caches an answer.
- `partitioning`: a JSON array of partition transforms. `sort`: a list of columns.
- `rls`: the row policies in order. The schema needs a `claim`, or the database refuses them.

A change of `rls` deletes the policies of the table and adds them again, in two calls. It isn't atomic. A change of the strategy, the partitioning, the sort or the policies makes the next sync rebuild the table. Import with the name.

## `data_proxy_access_policy`

A row that lets a subject read the data of a schema, limited to one unit when the row names it.

```hcl
resource "data_proxy_access_policy" "reader" {
  provider  = data-proxy
  schema    = data_proxy_schema.orders.name
  subject   = "reader@example.org"
  unit_type = "unit"
  unit_id   = "42"
}
```

Every attribute replaces the row. Without `unit_type` and `unit_id`, the row covers the whole schema. A create waits for the schema, for 5 minutes at most.

Manage static grants only, such as the grants of service accounts. An application that registers its own policies would delete rows that it didn't write. The table has no primary key, so a read fails when it finds equal rows.

Import with `schema/subject/unit_type/unit_id`, and an empty part for a missing unit: `terraform import data_proxy_access_policy.reader orders/reader@example.org/unit/42`.

<!-- nav:start -->
---

[Previous: Terraform provider](./terraform.md) | [Next: Terraform data sources](./terraform-data-sources.md) | [Index](./terraform.md) | [Home](./index.md)
<!-- nav:end -->
