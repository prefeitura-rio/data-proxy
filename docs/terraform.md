# Terraform provider

The `data_proxy` Terraform provider manages the configuration of Data Proxy as code. It manages the projects, the schemas, the tables, the row policies and the access policies through the PostgREST API. The provider is a Go module in `terraform/`, and it uses the Terraform Plugin Framework.

The provider has four resources and nine data sources. [Resources](terraform-resources.md) and [Data sources](terraform-data-sources.md) describe them, and [Provider development](dev/provider-development.md) says how to build and test it. The provider gets an access token from the identity provider with the OAuth 2.0 client credentials grant. It fetches a new token 30 seconds before the old one expires, so a long run keeps working with tokens that last 5 minutes.

## Configuration

The registry address is `<host>/<namespace>/data-proxy`, and the build sets it, so the provider has no host name. A local name has no underscore, so it's `data-proxy`. The resources are `data_proxy_*`, so each one needs `provider = data-proxy`. [Provider releases](dev/provider.md) says where the registry is. Each attribute has an environment variable. The provider uses the variable when the block doesn't set the attribute. An attribute in the block takes precedence over its variable, also when its value is an empty string.

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

Don't write the client secret in a `.tf` file. Set `DATA_PROXY_CLIENT_SECRET`, or give it to Terraform as a variable from your secret store.

<!-- nav:start -->
---

[Previous: Backups](operations/backups.md) | [Next: Terraform resources](./terraform-resources.md) | [Home](./index.md)
<!-- nav:end -->
