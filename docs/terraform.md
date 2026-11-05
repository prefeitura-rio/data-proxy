# Terraform provider

The `dataproxy` Terraform provider will manage the configuration of Data Proxy as code. It will manage the projects, the schemas, the tables, the row policies and the access policies through the PostgREST API. The provider is a Go module in `terraform/`, and it uses the Terraform Plugin Framework.

The provider is a skeleton now. It has the configuration block, but it has no resources and no data sources. It reads the values of the block and doesn't use them yet.

## Configuration

The registry address is `registry.terraform.io/prefeitura-rio/dataproxy`, and the type name is `dataproxy`. Every attribute is optional now.

| Attribute | Type | Content |
| --- | --- | --- |
| `endpoint` | string | The base address of the Data Proxy API. |
| `token_url` | string | The address of the token endpoint of the identity provider. |
| `client_id` | string | The client ID for the client credentials grant. |
| `client_secret` | string, sensitive | The client secret for the client credentials grant. Terraform hides it in the output. |
| `scopes` | list of strings | The scopes to request with the token. |

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

Don't write the client secret in a `.tf` file. Give it to Terraform as a variable from your secret store.

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

<!-- nav:start -->
---

[Previous: Backups](operations/backups.md) | [Home](./index.md)
<!-- nav:end -->
