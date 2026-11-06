# Terraform data sources

Every data source needs `provider = data-proxy`. A read fails when the object doesn't exist or the token can't see it.

| Data source | Arguments | Content |
| --- | --- | --- |
| `data_proxy_permissions` | none | The permissions of the token, such as `read_config`. |
| `data_proxy_project` | `name` | The project row. |
| `data_proxy_schema` | `name`, or `short_name` and `project` | The schema row, with its settings and claim. `project` defaults to `default`. |
| `data_proxy_table` | `name` | The table row and its `rls` list. |
| `data_proxy_schema_health` | `name` | Whether the sync created the schema (`schema_exists`) and its access policy table (`access_policy`), and the `view_count`. |
| `data_proxy_table_sync_state` | `name` | Whether the sync published the table (`synced`), its `signature` and `partition_count`. |
| `data_proxy_export_config` | none | The configuration as a JSON `document` grouped by project, with the empty projects. |
| `data_proxy_sync_config` | none | The flat `document` that the sync engine reads, with physical schema names as keys. |
| `data_proxy_config_history` | `schema` and `limit`, both optional | The newest changes in `entries`, newest first. The default `limit` is 100. |

## Examples

Read a schema that Terraform doesn't manage, and use its physical name:

```hcl
data "data_proxy_schema" "existing" {
  provider   = data-proxy
  short_name = "orders"
}

resource "data_proxy_table" "orders" {
  provider = data-proxy
  name     = "sales.dataset.orders"
  schema   = data.data_proxy_schema.existing.name
}
```

Start from an existing environment. The export has the same shape that the console exports and `import_config` reads:

```hcl
data "data_proxy_export_config" "this" {
  provider = data-proxy
}

output "projects" {
  value = keys(jsondecode(data.data_proxy_export_config.this.document).projects)
}
```

Wait for a schema in a later step of a pipeline:

```hcl
data "data_proxy_schema_health" "orders" {
  provider = data-proxy
  name     = "orders"
}

output "ready" {
  value = data.data_proxy_schema_health.orders.schema_exists && data.data_proxy_schema_health.orders.access_policy
}
```

<!-- nav:start -->
---

[Previous: Terraform resources](./terraform-resources.md) | [Index](./terraform.md) | [Home](./index.md)
<!-- nav:end -->
