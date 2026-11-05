# Provider releases

The Terraform provider has its own versions and its own releases. A chart release doesn't release the provider, and a provider release doesn't change the chart or production.

## How a release works

- The tag is `terraform-v<version>`, such as `terraform-v0.1.0`. The chart tags `helm-v<version>` stay separate.
- The workflow `terraform.yaml` lints the provider and runs its unit tests on each change under `terraform/`.
- A person starts a release. Run the workflow by hand on `main` with the input `version`, such as `0.1.0`. An empty input runs the checks only.
- The `release` job builds the provider for Linux, macOS and Windows, packs each binary in a zip archive, and writes `SHA256SUMS`.
- The job publishes the archives in a GitHub Release with the tag. The notes list the commit subjects under `terraform/` since the last provider tag.

A tag that exists already makes the job fail, so a version never changes after its release.

## Install a release

Download the archive for your system from the GitHub Release and put it in a Terraform [filesystem mirror](https://developer.hashicorp.com/terraform/cli/config/config-file#filesystem_mirror). The release has no signature, so the public Terraform Registry doesn't accept it. The checksums in `SHA256SUMS` let you check each archive.

## Commands

| Command | Work |
| --- | --- |
| `ci terraform release <version>` | Builds the archives, writes the checksums and publishes the GitHub Release. |

[CI and releases](./ci.md) describes the chart release.

<!-- nav:start -->
---

[Previous: Production deploys](./promote.md) | [Next: Operations](../operations/index.md) | [Index](./index.md) | [Home](../index.md)
<!-- nav:end -->
