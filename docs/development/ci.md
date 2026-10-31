# CI pipeline

The continuous integration (CI) pipeline is the workflow `.github/workflows/ci.yaml`. A second workflow, `sast.yaml`, runs the static application security testing (SAST). The scripts of the pipeline are in `scripts/ci.nu`. Run `ci` in the `checks` shell to call them.

## Triggers

The pipeline runs on a push to `main`, on a pull request, and on a manual start. A manual start has two inputs. `force_checks` runs every check and test without the saved task state, and the images and the release follow the changed files, as in a push. `force_full` does the same, builds every image, and starts the release job. The release job still needs a release-level commit and a change in the chart, so neither input forces a new chart version.

A push or pull request runs the pipeline only when it changes a path in the list `ci_paths` of the workflow. The list has `sync/`, `console/`, `k3d/`, `nix/`, `proxy/`, `helm/`, `k6/` and `scripts/`. It also has the workflows, `cog.toml`, the linter files of the repository root (`.sqlfluff`, `.sqlfluffignore`, `.hadolint.yaml` and `.nu-lint.toml`) and the devenv files. The container files of the sync, PostgreSQL, proxy and console images are in it too. A change in `docs/` doesn't start it.

The `concurrency` group is one for each branch, and a new run waits for the run before it. This order keeps two releases from overlapping.

## Jobs

| Job | Needs | Work |
| --- | --- | --- |
| `changes` | none | Runs `ci changes`. It compares the commit with the previous one. It writes which images need a build and whether the chart needs a release. |
| `checks` | none | Runs `all:lint:all` and `all:tests:unit` of the `checks` profile: every lint task and every `:unit` test task. It caches the virtual environment. |
| `test-sync`, `test-console`, `test-proxy` | none | Run the tasks `sync:tests:int`, `console:tests:int` and `proxy:tests:int` of the `suites` profile. Each task runs the integration tests of its component, and runs only if one of its input files changed. |
| `profile-sizes` | none | Runs `nix:lint:closure` of the `sizes` profile. It builds every profile and fails when one closure is larger than its limit. It runs only if a Nix input changed. No other job waits for it. |
| `build-images` | `changes` | Builds one image for each changed component and pushes nothing. It starts with the tests and fills the layer cache. It runs only on `main`. |
| `push-images` | `build-images` and all test jobs | Builds each image again from the cache, pushes it, and signs it. It runs only on `main`, and only after the tests pass, so no image of an untested commit reaches the registry. |
| `helm` | all earlier jobs, `push-images` included | Releases the chart. It runs only on `main`, when the chart or an image changed. |

Each job restores the task state before it runs. The `task-state-restore` action reads the state that the last green run of `main` saved, and `task-state-save` writes it. Only a run on `main` saves the state. [Tests](tests.md#run-only-what-changed) explains how a task decides to run.

The jobs `checks`, `test-sync`, `test-console` and `test-proxy` start at the same time as `changes`, because they use none of its output. The unit tests and the three integration suites therefore run in parallel on four runners.

The sync and console jobs start the build of the PostgreSQL test image and the pull of the Keycloak image in the background (`.github/actions/prewarm-images`), before the Nix install. The devenv tasks then find the layers in the cache. The console and checks jobs keep the uv cache and the pnpm store between runs (`.github/actions/cache-packages`), keyed by the lock files. Every job that runs devenv keeps the Nix fetcher cache (`.github/actions/cache-nix`), keyed by `devenv.lock`, so it doesn't download the flake inputs again.

The `changes` job and the `helm` job run in the `release` profile (`nix/profiles/release.nix`), which has only the `ci` script, `cog`, Helm and Git, and no C compiler. They don't evaluate the whole `checks` profile.

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
| `ci images resolve` | Asks the registry for the newest tag of each image, with `crane ls`, for all images at the same time, and writes the tags as step outputs. A missing tag falls back to `latest`, except for the PostgreSQL image, which fails. |
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
| `GITHUB_TOKEN` | Automatic | `push-images`, `helm` | Pushes the images and the chart to the GitHub Container Registry (GHCR) and creates the release page. |
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
