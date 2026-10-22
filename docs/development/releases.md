# Chart releases

CI releases the Helm chart from Conventional Commits with [cocogitto](https://docs.cocogitto.io/) (`cog`). The settings are in `cog.toml`, and `scripts/ci.nu` runs the steps. The steps use only git, `cog` and `helm`, so no forge is required.

## Version rules

`cog` reads the commit subjects since the previous `helm-v*` tag:

- A type with `!` uses the next major version.
- `feat` uses the next minor version.
- `fix` uses the next patch version.
- Other types create no release.

## Steps

| Command | Work |
| --- | --- |
| `ci version` | Prints the current version, the next version and the tag, or says that no release is due. It changes nothing. |
| `ci guard` | Skips the release when no chart file changed since the previous tag. |
| `ci release bump` | Runs `cog bump`. The hooks of `cog.toml` pin the image tags and set the chart version. Then `cog` writes `CHANGELOG.md`, commits `chore(version): helm-vX [skip ci]`, and tags the commit. |
| `ci chart package` | Packages the chart. |
| `ci chart publish` | Pushes the chart to the OCI registry. |
| `ci release push <tag>` | Pushes the release commit and the tag to `main` in one atomic push. |
| `ci release notes <tag>` | Writes the changelog section of the release to `packaged/notes.md`. |

## If a step fails

The chart is published before the push. A failed publish leaves no tag, so you can run the workflow again. A failed push leaves a published chart and no tag, and the next run publishes the same version again. A failed release page leaves the tag and the chart in place, so create the page by hand from `packaged/notes.md`.

## Protected main

The release commit goes straight to `main`. If `main` is protected, add the secret `RELEASE_TOKEN` with a token that may push to `main`. The release job uses it for the checkout, and it falls back to the default token when the secret is missing.

## Changelog

`CHANGELOG.md` has one section for each release, with the groups Breaking changes, Features and Fixes. The template is `scripts/changelog.tera`. Don't edit the file by hand.

## Run it locally

Run `cog bump --auto --dry-run` in the `checks` shell to print the next tag without a change. `ci version` prints the same result as outputs. `cog check --from-latest-tag` checks that the commit subjects follow Conventional Commits.

## Use another forge

Only the last workflow step is specific to GitHub. It runs `gh release create` with `packaged/notes.md`. On GitLab, a `release:` job can use the same file as its description. The registry address and the credentials come from the `CI_REGISTRY`, `CI_REGISTRY_USER`, `CI_REGISTRY_TOKEN` and `CI_REPOSITORY` variables.

<!-- nav:start -->
---

[Previous: Database migrations](migrations.md) | [Next: CI pipeline](ci.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
