# CI pipeline

The continuous integration (CI) pipeline is the workflow `.github/workflows/ci.yaml`. A second workflow, `sast.yaml`, runs the static application security testing (SAST). The scripts of the pipeline are in `scripts/ci.nu`. Run `ci` in the `checks` shell to call them.

## Triggers

The pipeline runs on a push to `main`, on a pull request, and on a manual start. A manual start has the input `force_full`, which builds every image and releases the chart.

A push or pull request runs the pipeline only when it changes a path in the list `ci_paths` of the workflow. The list has `sync/`, `console/`, `k3d/`, `nix/`, `proxy/`, `helm/`, `k6/` and `scripts/`. It also has the workflows, `cog.toml`, the linter files of the repository root (`.sqlfluff`, `.sqlfluffignore`, `.hadolint.yaml` and `.nu-lint.toml`) and the devenv files. The container files of the sync, PostgreSQL, proxy and console images are in it too. A change in `docs/` doesn't start it.

The `concurrency` group is one for each branch, and a new run waits for the run before it. This order keeps two releases from overlapping.

## Jobs

| Job | Needs | Work |
| --- | --- | --- |
| `changes` | none | Runs `ci changes`. It compares the commit with the previous one. It writes which images need a build and whether the chart needs a release. |
| `checks` | `changes` | Runs `lint:all` and `test:all` of the `checks` profile. It caches the virtual environment and the task state. |
| `test-sync`, `test-console`, `test-proxy` | `changes` | Run the integration suites of [Tests](tests.md). |
| `build-images` | all test jobs | Builds and pushes one image for each changed component. It runs only on `main`. |
| `helm` | all earlier jobs | Releases the chart. It runs only on `main`, when the chart or an image changed. |

A failed lint or test job stops the build and the release. The end-to-end (e2e) suite doesn't run in CI. Run it on the local cluster with `cluster k6 e2e`.

## Images

`ci changes` matches the changed files against the `change_pattern` of each image in the table `IMAGES` of `scripts/lib.nu`. The jobs build only the images that match.

| Image | Containerfile | Rebuilds when this path changes |
| --- | --- | --- |
| `data-proxy-sync` | `Containerfile.sync` | `sync/src/`, `sync/pyproject.toml`, `sync/uv.lock`, `console/migrations/` |
| `data-proxy-proxy` | `Containerfile.proxy` | `proxy/src/`, `proxy/lux.toml`, `proxy/lux.lock` |
| `data-proxy-jobs` | `Containerfile.jobs` | `Containerfile.jobs` |
| `data-proxy-postgres` | `Containerfile.postgres` | `Containerfile.postgres` |
| `data-proxy-console` | `Containerfile.console` | `console/ui/`, `console/notebooks/` |

Each image goes to `ghcr.io/<repository>-<name>`, for example `ghcr.io/prefeitura-rio/data-proxy-sync`. The tags are the short commit hash and `latest`. The PostgreSQL image adds the prefix `17-` to the hash, as in `17-ff86fc4`.

The k6 image of `Containerfile.k6` is for the local cluster only. CI doesn't build it.

## Pin the images in the chart

The release must not use `latest`. Two commands pin the tags:

| Command | Work |
| --- | --- |
| `ci images resolve` | Asks the registry for the newest tag of each image, with `crane ls`, and writes the tags as step outputs. A missing tag falls back to `latest`, except for the PostgreSQL image, which fails. |
| `ci images pin` | Rewrites the `image:` line of each Data Proxy image in `helm/values.yaml` with the tag from the environment. It fails when a line doesn't match. |

The `pre_bump_hooks` of `cog.toml` call `ci images pin`, so the release commit contains the pinned tags. See [Chart releases](releases.md) for the other steps.

## Other `ci` commands

| Command | Work |
| --- | --- |
| `ci git update` | Runs `git pull --rebase origin main`, so the release starts from the newest commit. |
| `ci chart login` | Logs in to the Open Container Initiative (OCI) registry with `helm registry login`. |
| `ci chart bump <version>` | Sets `version` in `helm/Chart.yaml`. The `pre_bump_hooks` of `cog.toml` call it. |

## SAST

`sast.yaml` runs on a push to `main`, on a pull request to `main`, and on a manual start. The job calls the shared action `prefeitura-rio/actions/sast`. The action sends the code to SonarQube and the findings to DefectDojo.

`sonar-project.properties` excludes `scripts/`, `k3d/`, `k6/`, `helm/ci/` and `helm/tests/` from the analysis. They contain tooling and tests.

## Secrets and variables

| Name | Kind | Used by | Purpose |
| --- | --- | --- | --- |
| `GITHUB_TOKEN` | Automatic | `build-images`, `helm` | Pushes the images and the chart to the GitHub Container Registry (GHCR) and creates the release page. |
| `RELEASE_TOKEN` | Secret, optional | `helm` | Pushes the release commit to a protected `main`. Without it, the job uses `GITHUB_TOKEN`. |
| `SONARQUBE_URL` | Variable | SAST | The address of the SonarQube server. |
| `SONARQUBE_TOKEN` | Secret | SAST | The token for SonarQube. |
| `DEFECTDOJO_URL` | Variable | SAST | The address of the DefectDojo server. |
| `DEFECTDOJO_TOKEN` | Secret | SAST | The token for DefectDojo. |
| `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET` | Secret | SAST | The Tailscale credentials that the shared action uses. |

## Settings for another forge

`ci.nu` reads these environment variables and uses the GitHub Actions values when they're missing.

| Variable | Default | Meaning |
| --- | --- | --- |
| `CI_OUTPUT_FILE` | `GITHUB_OUTPUT` | The file that receives the step outputs. Without it, the script prints them. |
| `CI_REPOSITORY` | `GITHUB_REPOSITORY` | The repository path, for example `prefeitura-rio/data-proxy`. |
| `CI_REGISTRY` | `ghcr.io` | The registry of the images and the chart. |
| `CI_REGISTRY_USER`, `CI_REGISTRY_TOKEN` | `GITHUB_ACTOR`, `GITHUB_TOKEN` | The registry login. |
| `CI_BOT_NAME`, `CI_BOT_EMAIL` | `github-actions[bot]` | The author of the release commit. |

<!-- nav:start -->
---

[Previous: Chart releases](releases.md) | [Next: Seed BigQuery data](seed.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
