# CI and releases

The workflow `.github/workflows/ci.yaml` checks, tests, builds and releases charts. `provider.yaml` publishes tagged providers. `sast.yaml` runs the security scan. The scripts are in `scripts/ci.nu`. Run `ci` in the `chart` shell to call them.

## Triggers

The pipeline runs on a push to `main`, on a pull request and on a manual start. A push or a pull request starts it only when it changes a path of the list `ci_paths`. The list has the component folders, `provider/`, `nix/`, `k3d/`, `k6/`, `migrations/`, `scripts/`, the workflows, the container files and the linter and devenv files. A change in `docs/` also starts the checks. A new run of a branch waits for the run before it, so two releases never overlap.

A manual start has three inputs. `force_checks` discards saved task state and runs every check and test. `force_full` does the same, builds every image and starts the release job, which still needs a release-level commit and a chart or image change. `docs_tag` publishes the docs of an existing chart release again.

## Jobs

```mermaid
{{#include ../diagrams/ci-jobs.mmd}}
```

| Job | Work |
| --- | --- |
| `changes` | Runs `ci detect changes`. It writes which images need a build and whether the chart needs a release. |
| `checks` | Runs `devenv --profile chart test`, `devenv --profile tools test` and the docs lint tasks. One matrix job each. |
| `test-sync`, `test-console`, `test-proxy` | Run `devenv --profile <component> test`. It runs the hooks in parallel, then the unit tests, then the integration tests, and stops the services. |
| `test-terraform` | Runs `devenv --profile provider test` without publication. |
| `build-images` | Builds each changed image and pushes nothing. It fills the layer cache while the tests run. |
| `push-images` | Waits for every test job. It builds each image again from the cache, pushes it and signs it, so no image of an untested commit reaches the registry. |
| `helm` | Releases the chart when the chart or an image changed. |
| `docs` | Builds the release bundle with mdBook and Repomix. It publishes version folders to `gh-pages`, then deploys the complete site with GitHub Pages actions. A manual start with `docs_tag` publishes a release again. |
| `release` in `provider.yaml` | Validates a provider tag, tests its exact commit, publishes with GoReleaser and updates the registry. See [Provider releases](../terraform.md#releases). |
| `cache-branch`, `cache-pr` | Delete the caches of a deleted branch and of a closed pull request. They're the only jobs that run on those events. |

The `checks` jobs restore compatible task state before their checks. Only a successful `main` run saves a snapshot. The compatibility key includes the profile, runner system and architecture, devenv version, lock files and task configuration. Restore keys never cross that boundary. The actions use SQLite backup to save a coherent task database, without generated-file manifests or live journal files. A missing or invalid snapshot gives the checks cold state.

The task database stays in the profile state directory, including when `devenv test` uses separate state for its services. `force_checks` and `force_full` bypass restore and clear the task database before checks. Integration tests always run. Task-state caching never skips their unit or integration commands.

Test image preparation starts in the background before Nix setup. CI evaluates the test environment while images build or load. `scripts/prewarm-images.nu` checks completion status and image presence before tests start. It has a bounded wait and prints build logs on failure. PostgreSQL and Keycloak use the same recipe-derived tags in CI and devenv. The Keycloak archive key includes runner architecture and the recipe, and save uses the verified tag from image preparation.

Package caches retain uv downloads, the pnpm store and Nix fetcher files. The provider gives `GOMODCACHE`, `GOCACHE` and `GOLANGCI_LINT_CACHE` stable paths under `.devenv/cache/`. They stay the same in normal shells and tests. The Go cache action reads those paths from the provider environment, and keys include Go version, runner architecture and dependency inputs.

The checks fetch full commit history. `cog check` validates recent Conventional Commit syntax and ignores merge commits. The repository still limits types through `cog.toml` and requires lowercase summary starts. The historical exception remains in `scripts/rules.nu`. Commit verification creates no release commit, tag or push.

## The docs site

mdBook builds the HTML and Markdown from `docs/`, in the order of `docs/SUMMARY.md`. Repomix packs the Markdown into `llms.txt`. The complete bundle contains the HTML, assets, chapter Markdown, `SUMMARY.md` and `llms.txt`.

After a successful chart release, the docs job checks the release tag and builds its bundle. A tag such as `helm-v2.21.0` publishes to `v2.21.0/` and `latest/` on this repository's `gh-pages` branch. The publishing action replaces files inside those folders and preserves the other versions. `latest/` follows the last publication, even when you publish an older release again. There is no version menu or custom JavaScript.

| Path | Content |
| --- | --- |
| [Latest docs](https://prefeitura-rio.github.io/data-proxy/latest/) | The last publication. |
| [Latest LLM bundle](https://prefeitura-rio.github.io/data-proxy/latest/llms.txt) | All chapters in one Markdown file. |
| `/data-proxy/v2.21.0/index.html` | The docs of that chart release. |
| `/data-proxy/v2.21.0/llms.txt` | The LLM bundle of that chart release. |

These are static paths, not API endpoints. For another release, replace `v2.21.0` with its version. mdBook uses HTML page paths such as `/data-proxy/latest/security/roles.html`.

The job uses `GITHUB_TOKEN` with `contents: write` to publish `gh-pages`. It then reads the complete branch, uploads it with `upload-pages-artifact` and deploys it with `deploy-pages`. Deployment needs `pages: write`, `id-token: write` and the `github-pages` environment. The job needs no deploy key and runs no publication on a pull request. A concurrency group serializes publication and deployment.

Set **Settings → Pages → Build and deployment → Source** to **GitHub Actions** once. Configure the `github-pages` environment to permit deployments from `main`. GitHub skips branch-based Pages builds for pushes with `GITHUB_TOKEN`, so CI deploys the artifact explicitly.

To publish a release again, start CI on `main` with `docs_tag` set to its tag. To build the bundle locally, run `devenv --profile docs tasks run docs:build:site`. It writes `.book/html/`.

## Images

`ci detect changes` matches the files that changed since the last `helm-v*` tag against the table `IMAGES` in `scripts/lib.nu`. A push to `main` compares with the last tag. A pull request compares with its base. Each image goes to `ghcr.io/<repository>-<name>` with the short commit hash and `latest` as tags. The PostgreSQL image adds the prefix `18-` to the hash.

| Image | Rebuilds when this changes |
| --- | --- |
| `data-proxy-sync` | `sync/src/`, `sync/pyproject.toml`, `sync/uv.lock`, `migrations/` |
| `data-proxy-proxy` | `proxy/src/`, `proxy/lux.toml`, `proxy/lux.lock` |
| `data-proxy-console` | `console/ui/`, `console/notebooks/` |
| `data-proxy-jobs`, `data-proxy-postgres` | The Containerfile of the image |

## Chart releases

CI releases the Helm chart from Conventional Commits with `cog`. `cog` reads the commit subjects since the last `helm-v*` tag. A type with `!` makes a major version, `feat` a minor version and `fix` a patch version. Other types make no release.

| Command | Work |
| --- | --- |
| `ci plan chart` | Finds the newest tag of each image, reads the next version, and decides whether a release is due. It changes no file. A release is due when a chart file or an image path changed since the last tag. A commit outside the provider and the docs can release. |
| `ci release chart` | Runs `cog bump --package helm-v --auto`. The hook of `cog.toml` runs `ci prepare chart`, which pins the images and sets the chart version and the DBOS version. `cog` writes `CHANGELOG.md`, commits and tags. |
| `ci package chart` | Package the chart. |
| `ci publish chart <tag>` | Publish the chart, and write the release notes. |

A commit that changes only `provider/`, or `docs/` never releases the chart, even as a `feat` or a `fix`. Cocogitto also excludes those commits from the chart version and changelog.

The job pushes the commit and the tag with `git push --atomic` before it publishes the chart. A failed push publishes nothing, so run the workflow again. After a failed publish, run `ci package chart` and `ci publish chart` at the tag. `GITHUB_TOKEN` can't push to `main`. The job pushes with the deploy key `bot` (the secret `RELEASE_DEPLOY_KEY` of the environment `release`), which `protect-main` lists as a bypass.

Staging follows every chart. Production follows the chart tag `prod`, which a reviewer moves. See [Production deploys](./promote.md).

`ci.nu` reads `CI_REPOSITORY`, `CI_REGISTRY`, `CI_REGISTRY_USER`, `CI_REGISTRY_TOKEN`, `CI_BOT_NAME`, `CI_BOT_EMAIL` and `CI_OUTPUT_FILE`, and it uses the GitHub Actions values when they're missing.

## Provider releases

Cocogitto owns component versions, changelogs and tags. Its package IDs match the tag prefixes: `v` for the provider and `helm-v` for the product. The empty package separator preserves the existing Helm tags and gives the provider standard `vX.Y.Z` tags. No global version tag exists.

The provider package contains `provider/` and writes `provider/CHANGELOG.md`. The product package excludes `provider/` and `docs/`, writes `CHANGELOG.md` and runs chart preparation hooks. Both packages use the same commit-type policy in `cog.toml`.

The workflow `provider.yaml` starts only for `v*` tag pushes. It rejects non-stable versions, commits outside main history and versions at or below any published provider version. It tests the exact tagged commit before publication. Provider release commits must enter reviewed main history before their tags reach the remote.

GoReleaser comes from nixpkgs through the provider profile. Its project name is `data-proxy`. Terraform requires the longer binary name `terraform-provider-data-proxy_v<version>`. GoReleaser builds five ZIP archives, calculates checksums and signs the checksum manifest. Cocogitto supplies the release notes. GoReleaser never selects versions or creates tags.

The signing step passes the key passphrase through standard input, outside command arguments. The workflow keeps the private key in an isolated temporary keyring and removes it afterward. Registry publication follows successful GitHub publication. Release runs share one concurrency group to serialize registry writes.

The registry reads every published provider release through GitHub pagination. It retains historical `terraform-v*` tags and their original artifact URLs. New releases use `v*` tags and actual GoReleaser asset names. Duplicate versions and missing artifacts stop publication. See [Provider release commands and migration](../terraform.md#releases).

## Security scan

`sast.yaml` calls the shared action `prefeitura-rio/actions/sast`, which sends the code to SonarQube and the findings to DefectDojo. It needs the variables `SONARQUBE_URL` and `DEFECTDOJO_URL` and the secrets `SONARQUBE_TOKEN`, `DEFECTDOJO_TOKEN`, `TS_OAUTH_CLIENT_ID` and `TS_OAUTH_SECRET`. `sonar-project.properties` excludes the tooling and the tests.

The run on `main` fails at the SonarQube quality gate. The log doesn't name the condition, so read the project dashboard on the SonarQube server. Checkov reports five `CKV_SECRET_6` findings although `.checkov.yaml` skips that check, so the action may not read the file.

The rule set `protect-main` protects `main`. It requires a pull request, one approval and the checks `Lint and unit tests`, `Sync integration tests`, `Console integration tests` and `Proxy integration tests`. Only the repository admin role and deploy keys bypass it.
