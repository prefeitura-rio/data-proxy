# Terraform provider

The `data_proxy` Terraform provider manages the configuration of Data Proxy as code. It manages the projects, the schemas, the tables, the row policies and the access policies through the PostgREST API. The provider is a Go module in `terraform/`, and it uses the Terraform Plugin Framework.

The provider has four resources and nine data sources. [Resources](#resources) and [Data sources](#data-sources) describe them, and [Develop](#develop) says how to build and test it, and [Releases](#releases) says how it ships. The provider gets an access token from the identity provider with the OAuth 2.0 client credentials grant. It fetches a new token 30 seconds before the old one expires, so a long run keeps working with tokens that last 5 minutes.

## Configuration

The registry address is `<host>/<namespace>/data-proxy`, and the build sets it, so the provider has no host name. A local name has no underscore, so it's `data-proxy`. The resources are `data_proxy_*`, so each one needs `provider = data-proxy`. [Releases](#releases) says where the registry is. Each attribute has an environment variable. The provider uses the variable when the block doesn't set the attribute. An attribute in the block takes precedence over its variable, also when its value is an empty string.

| Attribute | Variable | Type | Content |
| --- | --- | --- | --- |
| `endpoint` | `DATA_PROXY_ENDPOINT` | string, required | The base address of the Data Proxy API. |
| `token_url` | `DATA_PROXY_TOKEN_URL` | string, required | The address of the token endpoint of the identity provider. |
| `client_id` | `DATA_PROXY_CLIENT_ID` | string, required | The client ID for the client credentials grant. |
| `client_secret` | `DATA_PROXY_CLIENT_SECRET` | string, required, sensitive | The client secret for the client credentials grant. Terraform hides it in the output. |
| `scopes` | `DATA_PROXY_SCOPES` | list of strings | The scopes to request with the token. The variable separates them with commas or spaces. |

A required value must come from the block or from its variable. The provider fails with one error for each value that's missing.

When it starts, the provider gets a token and calls `GET /rpc/permissions`. It fails when the identity provider refuses the secret, when the API refuses the token, or when the token lacks `read_config`. Every resource and data source reads the configuration. The database checks the write permissions, such as `create_config`, `edit_config` and `delete_config`, on each request. The error of a refused request names the permission that it needed. The [roles page](security/roles.md) lists the permissions of each role.

```hcl
terraform {
  required_providers {
    data-proxy = {
      source = "registry.example.com/example/data-proxy"
    }
  }
}

provider "data-proxy" {
  endpoint  = "https://data-proxy.example.com"
  token_url = "https://id.example.com/realms/example/protocol/openid-connect/token"
  client_id = "terraform"
  scopes    = ["openid"]
}
```

`terraform init` downloads the archive and checks the signature and the checksum. Don't write the client secret in a `.tf` file. Set `DATA_PROXY_CLIENT_SECRET`, or give it to Terraform as a variable from your secret store.

## Resources

Every resource of the provider needs `provider = data-proxy`. A refused write names the permission that it needed, such as `create_config`.

### `data_proxy_project`

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

### `data_proxy_schema`

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
| `name` | The physical name: `<project>__<short_name>`, also in `default`. Tables use it. |
| `source`, `settings`, `claim`, `encrypted` | The columns of the schema. `settings` is a JSON object, such as `jsonencode({...})`. |
| `deletion_protection` | Refuses the delete while it's `true`, which is the default. Only Terraform reads it. |

A create waits until the sync provisions the schema, for 5 minutes at most. A delete waits until the sync drops it, for 10 minutes. Set `timeouts { create = "0s" }` or `delete = "0s"` to skip the wait. A replace or a delete drops the data, so the plan shows it.

Import with the physical name. An imported schema has deletion protection.

### Names

The console and the provider check a new name before they send a request. The database doesn't check these rules, so an old name stays valid.

- A project name can't be `access`, `console`, `rbac`, `dbos`, `public`, `duckdb`, `rpc`, `docs` or `v1`. A URL or a PostgreSQL schema of the system uses these names.
- A schema short name has no two underscores in a row.
- `<project>__<short_name>` has at most 56 bytes. The project `default` counts with its name, so a short name in `default` has at most 47 bytes.

### `data_proxy_table`

A table that the sync copies from the source into a schema.

```hcl
resource "data_proxy_table" "orders" {
  provider = data-proxy
  name     = "sales.dataset.orders"
  schema   = data_proxy_schema.orders.name
  sort     = ["order_id"]

  rls {
    column_name = "area"
    unit_type   = "area"
  }

  rls {
    condition {
      column_name = "region_id"
      unit_type   = "region"
    }
    condition {
      column_name = "unit_id"
      unit_type   = "unit"
    }
  }
}
```

- `name` and `schema`: a change replaces the table and syncs it again.
- `strategy`: `full` (default) or `partitioned`. `n` and `fallback` need `partitioned`.
- `cache_ttl`: the seconds that the proxy caches an answer.
- `partitioning`: a JSON array of partition transforms. `sort`: a list of columns.
- `rls`: a row policy. The schema needs a `claim`, or the database refuses it. Policies are alternatives. Write one condition as `column_name` and `unit_type`. Write two or more conditions that all must match as `condition` blocks. The two forms do not mix, and one `condition` block is refused. The order of the blocks stays the same.

A change of `rls` replaces the policies of the table in one call, so a failure leaves the old policies. A change of the strategy, the partitioning, the sort or the policies makes the next sync rebuild the table. Import with the name.

### `data_proxy_grant`

A grant of the access policy. It lets a subject read the rows of one unit in a scope: a project, a schema or a table.

```hcl
resource "data_proxy_grant" "reader" {
  provider  = data-proxy
  scope     = "${data_proxy_schema.orders.project}.${data_proxy_schema.orders.short_name}"
  subject   = "reader@example.org"
  unit_type = "unit"
  unit_id   = "42"
}
```

The scope is `*` for every project, or `project`, `project.schema` or `project.schema.table`. A grant covers its scope and what's below it. The unit `*` of type `*` stands for every row. Every attribute replaces the row.

Manage static grants only, such as the grants of service accounts. An application that registers its own grants would delete rows that it didn't write.

Import with `scope/subject/unit_type/unit_id`: `terraform import data_proxy_grant.reader default.orders/reader@example.org/unit/42`.

## Data sources

Every data source needs `provider = data-proxy` too. A read fails when the object doesn't exist or the token can't see it.

| Data source | Arguments | Content |
| --- | --- | --- |
| `data_proxy_permissions` | none | The permissions of the token, such as `read_config`. |
| `data_proxy_project` | `name` | The project row. |
| `data_proxy_schema` | `name`, or `short_name` and `project` | The schema row, with its settings and claim. `project` defaults to `default`. |
| `data_proxy_table` | `name` | The table row and its `rls` list. |
| `data_proxy_schema_health` | `name` | Whether the sync created the schema (`schema_exists`), and the `view_count`. |
| `data_proxy_table_sync_state` | `name` | Whether the sync published the table (`synced`), its `signature` and `partition_count`. |
| `data_proxy_export_config` | none | The configuration as a JSON `document` grouped by project, with the empty projects. |
| `data_proxy_sync_config` | none | The flat `document` that the sync engine reads, with physical schema names as keys. |
| `data_proxy_config_history` | `schema` and `limit`, both optional | The newest changes in `entries`, newest first. The default `limit` is 100. |

### Examples

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
  value = data.data_proxy_schema_health.orders.schema_exists
}
```

## Develop

The provider is a Go module in `terraform/`. This section says how to build, run and test it.

### Build and test

Run the commands in `terraform/`. The module has its own `go.mod`, so the commands don't build the rest of the repository.

```bash
cd terraform
go build ./...
go vet ./...
go test ./...
```

### Local development

Terraform downloads a provider from the registry. To test your local binary, add a `dev_overrides` block to the command-line configuration file `~/.terraformrc`. The block tells Terraform to load the provider from a folder on your machine.

1. Build the binary with the name that Terraform expects. `terraform/bin/` is in `.gitignore`.

   ```bash
   cd terraform
   go build -o bin/terraform-provider-data-proxy .
   ```

2. Add the block to `~/.terraformrc`. Write the absolute path of `terraform/bin` in your clone.

   ```hcl
   provider_installation {
     dev_overrides {
       "registry.example.com/example/data-proxy" = "/home/you/data-proxy/terraform/bin"
     }
     direct {}
   }
   ```

3. Run `terraform validate` or `terraform plan` in a folder with a configuration that uses the provider. Don't run `terraform init` for this provider. Terraform prints a warning that says the override is active.

The `direct {}` block keeps the normal download for every other provider. Build the binary again after each change. Terraform loads the new binary on the next command. To use the file only in one shell, set `TF_CLI_CONFIG_FILE` to its path. Remove the block when you want the registry version again.

#### Acceptance tests

An acceptance test runs the provider through Terraform against a real API. It's a `TestAcc` function in `terraform/internal/provider/`. It runs only when `TF_ACC` is set, so `go test ./...` compiles it and skips it.

```bash
devenv --profile terraform tasks run tf:test:int
```

The task runs `devenv --profile terraform-acc test`. That profile starts the console stack: PostgreSQL, SeaweedFS, Keycloak, PostgREST and the console. It sets `TF_ACC=1` and the `DATA_PROXY_*` variables. The provider calls the local PostgREST. It gets its token from the local Keycloak with the test client `terraform`. That client has the role `dp_admin` and the `schemas` claim `*`. The stack needs Podman, as the [tests page](dev/tests.md#the-test-stack) says.

To run one test, start the console stack and set the variables yourself. PostgREST listens on port 13000 and Keycloak on port 18180 when the ports are free. Run `go test` in the `terraform-acc` shell, because the tests call OpenTofu.

```bash
devenv --profile console up -d
devenv processes wait
export TF_ACC=1 DATA_PROXY_ENDPOINT=http://127.0.0.1:13000 DATA_PROXY_CLIENT_ID=terraform DATA_PROXY_CLIENT_SECRET=test-secret
export DATA_PROXY_TOKEN_URL=http://localhost:18180/realms/dev/protocol/openid-connect/token
devenv --profile terraform shell -- go -C terraform test -count=1 -run TestAccPermissionsDataSource ./...
```

A new acceptance test uses the helpers in `acceptance_test.go`. Give `testAccPreCheck` to `PreCheck` and `testAccProviderFactories` to `ProtoV6ProviderFactories`. Name each object with `testAccName`, which returns `tfacc` and 10 random letters and digits. Register `testAccCleanup` for each object, so a failed step leaves no row behind. Register a parent before its children, because the cleanups run in reverse order.

The client `terraform` has the `schemas` claim `*`, so a test can add projects and schemas. The stack runs no sync worker, so a schema never gets provisioned. Set `timeouts { create = "0s" }` on a schema to skip the wait. The schema `test` is provisioned at start, so use it for an access policy.

## Releases

The Terraform provider has its own versions and its own releases. A chart release doesn't release the provider, and a provider release doesn't change the chart or production.

### How a release happens

CI runs on every push to `main`. When `repo` and `test-terraform` pass, the job `terraform` of CI runs the action `terraform-release` on the same commit. The action decides whether a release is due. The job doesn't wait for the chart release. A failed provider release turns the run red, so the chart stays out of staging.

1. `ci plan terraform` reads the commits that changed `terraform/` since the last tag `terraform-v<version>`.
2. A `feat` or a breaking commit raises the minor version, and a `fix` raises the patch. Other types give no release. The major version stays 0 until a person sets it.
3. `ci release terraform` builds the provider for Linux, macOS and Windows, packs each binary in a zip archive, and signs the checksums with the GPG key.
4. It creates the GitHub Release `terraform-v<version>` with the archives, the checksum file and its signature. The notes list the commit subjects under `terraform/`.
5. `ci publish terraform` writes the registry files from every release and uploads them to a bucket.

A tag that exists already makes the job fail, so a version never changes after its release. To release a version by hand, start CI with the input `provider_version`.

### The registry

The registry is a set of static JSON files, as the [registry protocol](https://developer.hashicorp.com/terraform/internet-documentation/provider-registry-protocol) describes. The release workflow writes them to a public bucket. The ingress of Data Proxy sends `/.well-known/terraform.json` and `/v1/providers/` to that bucket, so the host of the API also serves the registry. The archives stay in GitHub Releases, and the JSON files point to them.

The provider and the scripts have no host name. Every name comes from a variable.

| Variable | Where | Content |
| --- | --- | --- |
| `TERRAFORM_REGISTRY_HOST` | GitHub repository variable | The host that serves the registry. The provider reports `<host>/<namespace>/data-proxy` as its address. |
| `TERRAFORM_REGISTRY_NAMESPACE` | GitHub repository variable | The namespace in the address and in the registry paths. |
| `TERRAFORM_REGISTRY_BUCKET` | GitHub repository variable | The bucket that stores the registry files. |
| `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT` | GitHub repository variables | The identity that writes the bucket. |
| `GPG_PRIVATE_KEY`, `GPG_PASSPHRASE` | GitHub secrets | The key that signs the checksums. |
| `ingress.registry.bucket` | Chart value | The bucket that the ingress serves. An empty value adds no route. |

Terraform and OpenTofu refuse a provider from a registry unless a GPG key signs its checksums. The workflow gets the public key from the private key, so the secrets are the only key input.

### Set up the registry

1. Create a GPG key without an expiry date, and add its private key and its passphrase as the secrets `GPG_PRIVATE_KEY` and `GPG_PASSPHRASE`.
2. In the infra repository, set the variable `terraform_registry` of the GCP module with the repository that publishes. Apply the module. It creates the bucket, the service account and the identity pool.
3. Set `terraform_registry_enabled` of the k8s module, and apply it. It adds the route values for the ingress and the Istio objects for the storage host.
4. Copy the outputs of the GCP module into the GitHub variables of the table above. The workflow does nothing while `TERRAFORM_REGISTRY_HOST` is empty.

The route needs a chart version that has `ingress.registry`. Staging gets each chart first, and production waits for an approval.

### Roll back and rotate

- A bad release stays in the registry. Fix it with a new `fix` commit. Or delete the GitHub Release and run `ci publish terraform` again, which drops the version from the files.
- To rotate the key, add the new key and rerun `ci publish terraform`. A release that the old key signed fails to install with the new key, so keep the old key until you release again.

### Commands

| Command | Work |
| --- | --- |
| `ci plan terraform` | Writes `release` and `version` from the commits since the last provider tag. |
| `ci release terraform <version>` | Builds, signs and publishes the GitHub Release. |
| `ci publish terraform` | Writes the registry files from the releases and uploads them to the bucket. |

[CI and releases](dev/ci.md) describes the chart release.

<!-- nav:start -->
---

[Previous: Backups](operations/backups.md) | [Home](./index.md)
<!-- nav:end -->
