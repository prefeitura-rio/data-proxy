# Provider releases

The Terraform provider has its own versions and its own releases. A chart release doesn't release the provider, and a provider release doesn't change the chart or production.

## How a release happens

CI runs on every push to `main`. When `repo` and `test-terraform` pass, the job `terraform` of CI calls the workflow `terraform.yaml` on the same commit. The workflow decides whether a release is due. The job doesn't wait for the chart release. A failed provider release turns the run red, so the chart stays out of staging.

1. `ci terraform next` reads the commits that changed `terraform/` since the last tag `terraform-v<version>`.
2. A `feat` or a breaking commit raises the minor version, and a `fix` raises the patch. Other types give no release. The major version stays 0 until a person sets it.
3. `ci terraform release` builds the provider for Linux, macOS and Windows, packs each binary in a zip archive, and signs the checksums with the GPG key.
4. It creates the GitHub Release `terraform-v<version>` with the archives, the checksum file and its signature. The notes list the commit subjects under `terraform/`.
5. `ci terraform registry` writes the registry files from every release and uploads them to a bucket.

A tag that exists already makes the job fail, so a version never changes after its release. To release a version by hand, run the workflow `terraform.yaml` with the input `version`.

## The registry

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

## Set up the registry

1. Create a GPG key without an expiry date, and add its private key and its passphrase as the secrets `GPG_PRIVATE_KEY` and `GPG_PASSPHRASE`.
2. In the infra repository, set the variable `terraform_registry` of the GCP module with the repository that publishes. Apply the module. It creates the bucket, the service account and the identity pool.
3. Set `terraform_registry_enabled` of the k8s module, and apply it. It adds the route values for the ingress and the Istio objects for the storage host.
4. Copy the outputs of the GCP module into the GitHub variables of the table above. The workflow does nothing while `TERRAFORM_REGISTRY_HOST` is empty.

The route needs a chart version that has `ingress.registry`. Staging gets each chart first, and production waits for an approval.

## Use a release

```hcl
terraform {
  required_providers {
    data-proxy = {
      source = "registry.example.com/example/data-proxy"
    }
  }
}
```

Replace the source with `<host>/<namespace>/data-proxy` of your registry. A local name has no underscore, and Terraform reads the provider of `data_proxy_table` from the part before the first underscore. So each resource needs `provider = data-proxy`. `terraform init` downloads the archive and checks the signature and the checksum.

## Roll back and rotate

- A bad release stays in the registry. Fix it with a new `fix` commit. Or delete the GitHub Release and run `ci terraform registry` again, which drops the version from the files.
- To rotate the key, add the new key and rerun `ci terraform registry`. A release that the old key signed fails to install with the new key, so keep the old key until you release again.

## Commands

| Command | Work |
| --- | --- |
| `ci terraform next` | Writes `release` and `version` from the commits since the last provider tag. |
| `ci terraform release <version>` | Builds, signs and publishes the GitHub Release. |
| `ci terraform registry` | Writes the registry files from the releases and uploads them to the bucket. |

[CI and releases](./ci.md) describes the chart release.

<!-- nav:start -->
---

[Previous: Production deploys](./promote.md) | [Next: Provider development](./provider-development.md) | [Index](./index.md) | [Home](../index.md)
<!-- nav:end -->
