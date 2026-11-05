# Terraform provider

The `dataproxy` Terraform provider will manage the configuration of Data Proxy as code. It will manage the projects, the schemas, the tables, the row policies and the access policies through the PostgREST API. The provider is a Go module in `terraform/`, and it uses the Terraform Plugin Framework.

The provider has the configuration block and the `dataproxy_permissions` data source, and no resources yet. It gets an access token from the identity provider with the OAuth 2.0 client credentials grant. It fetches a new token 30 seconds before the old one expires, so a long run keeps working with tokens that last 5 minutes.

## Configuration

The registry address is `registry.terraform.io/prefeitura-rio/dataproxy`, and the type name is `dataproxy`. Each attribute has an environment variable. The provider uses the variable when the block doesn't set the attribute. An attribute in the block takes precedence over its variable, also when its value is an empty string.

| Attribute | Variable | Type | Content |
| --- | --- | --- | --- |
| `endpoint` | `DATAPROXY_ENDPOINT` | string, required | The base address of the Data Proxy API. |
| `token_url` | `DATAPROXY_TOKEN_URL` | string, required | The address of the token endpoint of the identity provider. |
| `client_id` | `DATAPROXY_CLIENT_ID` | string, required | The client ID for the client credentials grant. |
| `client_secret` | `DATAPROXY_CLIENT_SECRET` | string, required, sensitive | The client secret for the client credentials grant. Terraform hides it in the output. |
| `scopes` | `DATAPROXY_SCOPES` | list of strings | The scopes to request with the token. The variable separates them with commas or spaces. |

A required value must come from the block or from its variable. The provider fails with one error for each value that's missing.

When it starts, the provider gets a token and calls `GET /rpc/permissions`. It fails when the identity provider refuses the secret, when the API refuses the token, or when the token lacks `read_config`. Every resource and data source reads the configuration. The database checks the write permissions, such as `create_config`, `edit_config` and `delete_config`, on each request. The error of a refused request names the permission that it needed. The [roles page](security/roles.md) lists the permissions of each role.

```hcl
terraform {
  required_providers {
    dataproxy = {
      source = "registry.terraform.io/prefeitura-rio/dataproxy"
    }
  }
}

provider "dataproxy" {
  endpoint  = "https://data-proxy.example.com"
  token_url = "https://id.example.com/realms/example/protocol/openid-connect/token"
  client_id = "terraform"
  scopes    = ["openid"]
}
```

Don't write the client secret in a `.tf` file. Set `DATAPROXY_CLIENT_SECRET`, or give it to Terraform as a variable from your secret store.

## Data sources

`dataproxy_permissions` reads the permissions of the token, from `GET /rpc/permissions`. It has no arguments. Its attribute `permissions` is a set of the names in `rbac.toml`, such as `read_config`.

```hcl
data "dataproxy_permissions" "this" {}

output "can_delete" {
  value = contains(data.dataproxy_permissions.this.permissions, "delete_config")
}
```

## Build and test

Run the commands in `terraform/`. The module has its own `go.mod`, so the commands don't build the rest of the repository.

```bash
cd terraform
go build ./...
go vet ./...
go test ./...
```

## Local development

Terraform downloads a provider from the registry. To test your local binary, add a `dev_overrides` block to the command-line configuration file `~/.terraformrc`. The block tells Terraform to load the provider from a folder on your machine.

1. Build the binary with the name that Terraform expects. `terraform/bin/` is in `.gitignore`.

   ```bash
   cd terraform
   go build -o bin/terraform-provider-dataproxy .
   ```

2. Add the block to `~/.terraformrc`. Write the absolute path of `terraform/bin` in your clone.

   ```hcl
   provider_installation {
     dev_overrides {
       "registry.terraform.io/prefeitura-rio/dataproxy" = "/home/you/data-proxy/terraform/bin"
     }
     direct {}
   }
   ```

3. Run `terraform validate` or `terraform plan` in a folder with a configuration that uses the provider. Don't run `terraform init` for this provider. Terraform prints a warning that says the override is active.

The `direct {}` block keeps the normal download for every other provider. Build the binary again after each change. Terraform loads the new binary on the next command. To use the file only in one shell, set `TF_CLI_CONFIG_FILE` to its path. Remove the block when you want the registry version again.

### Acceptance tests

An acceptance test runs the provider through Terraform against a real API. It's a `TestAcc` function in `terraform/internal/provider/`. It runs only when `TF_ACC` is set, so `go test ./...` compiles it and skips it.

```bash
devenv --profile terraform tasks run tf:test:int
```

The task runs `devenv --profile terraform-acc test`. That profile starts the console stack: PostgreSQL, SeaweedFS, Keycloak, PostgREST and the console. It sets `TF_ACC=1` and the `DATAPROXY_*` variables. The provider calls the local PostgREST and gets its token from the local Keycloak with the test client `admin`, whose role is `dp_admin`. The stack needs Podman, as the [tests page](dev/tests.md#the-test-stack) says.

To run one test, start the console stack and set the variables yourself. PostgREST listens on port 13000 and Keycloak on port 18180 when the ports are free. Run `go test` in the `terraform-acc` shell, because the tests call OpenTofu.

```bash
devenv --profile console up -d
devenv processes wait
export TF_ACC=1 DATAPROXY_ENDPOINT=http://127.0.0.1:13000 DATAPROXY_CLIENT_ID=admin DATAPROXY_CLIENT_SECRET=test-secret
export DATAPROXY_TOKEN_URL=http://localhost:18180/realms/dev/protocol/openid-connect/token
devenv --profile terraform shell -- go -C terraform test -count=1 -run TestAccPermissionsDataSource ./...
```

A new acceptance test uses the helpers in `acceptance_test.go`. Give `testAccPreCheck` to `PreCheck` and `testAccProviderFactories` to `ProtoV6ProviderFactories`. Name each object with `testAccName`, which returns `tfacc` and 10 random letters and digits. Register `testAccCleanup` for each object, so a failed step leaves no row behind. The `schemas` claim of `admin` is a fixed list. A test that creates a schema with a new name needs a client whose claim allows that name.

<!-- nav:start -->
---

[Previous: Backups](operations/backups.md) | [Home](./index.md)
<!-- nav:end -->
