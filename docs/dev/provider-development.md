# Provider development

This page says how to build, run and test the Terraform provider in `terraform/`. [Terraform provider](../terraform.md) describes what it does, and [Provider releases](./provider.md) describes how it ships.

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

### Acceptance tests

An acceptance test runs the provider through Terraform against a real API. It's a `TestAcc` function in `terraform/internal/provider/`. It runs only when `TF_ACC` is set, so `go test ./...` compiles it and skips it.

```bash
devenv --profile terraform tasks run tf:test:int
```

The task runs `devenv --profile terraform-acc test`. That profile starts the console stack: PostgreSQL, SeaweedFS, Keycloak, PostgREST and the console. It sets `TF_ACC=1` and the `DATA_PROXY_*` variables. The provider calls the local PostgREST. It gets its token from the local Keycloak with the test client `terraform`. That client has the role `dp_admin` and the `schemas` claim `*`. The stack needs Podman, as the [tests page](./tests.md#the-test-stack) says.

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

<!-- nav:start -->
---

[Previous: Provider releases](./provider.md) | [Next: Operations](../operations/index.md) | [Index](./index.md) | [Home](../index.md)
<!-- nav:end -->
