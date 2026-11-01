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
| `repo` | none | Runs `repo:lint` and `repo:test:unit` of the `repo` profile: every lint task and every `:unit` test task. It caches the virtual environment. |
| `test-sync`, `test-console`, `test-proxy` | none | Run the tasks `sync:test:int`, `console:test:int` and `proxy:test:int` of the `int` profile. Each task runs the integration tests of its component, and runs only if one of its input files changed. |
| `profile-sizes` | none | Runs `nix:lint:closure` and `nix:lint:cmd` of the `nix` profile. The first builds every profile and fails when one closure is larger than its limit. The second fails when a `devenv` command in the docs names a profile or a task that doesn't exist. They run only if a Nix input or a docs file changed. No other job waits for it. |
| `build-images` | `changes` | Builds one image for each changed component and pushes nothing. It starts with the tests and fills the layer cache. It runs only on `main`. |
| `push-images` | `build-images` and all test jobs | Builds each image again from the cache, pushes it, and signs it. It runs only on `main`, and only after the tests pass, so no image of an untested commit reaches the registry. |
| `helm` | all earlier jobs, `push-images` included | Releases the chart. It runs only on `main`, when the chart or an image changed. |

Each job restores the task state before it runs. The `task-state-restore` action reads the state that the last green run of `main` saved, and `task-state-save` writes it. Only a run on `main` saves the state. [Tests](tests.md#run-only-what-changed) explains how a task decides to run.

The jobs `repo`, `test-sync`, `test-console` and `test-proxy` start at the same time as `changes`, because they use none of its output. The unit tests and the three integration suites therefore run in parallel on four runners.

The sync and console jobs start the build of the PostgreSQL test image in the background (`.github/actions/prewarm-images`), before the Nix install. The console job also restores the test Keycloak image from the cache (a 399 MB archive, keyed by `Containerfile.kc`), or builds it. A run on `main` that found no copy saves it (`.github/actions/save-images`). The Containerfile runs the server build once, so Keycloak starts in 20 s and not in 33 s on a runner with two cores. The devenv tasks then find the images. The console and checks jobs keep the uv cache and the pnpm store between runs (`.github/actions/cache-packages`), keyed by the lock files. Every job that runs devenv keeps the Nix fetcher cache (`.github/actions/cache-nix`), keyed by `devenv.lock`, so it doesn't download the flake inputs again. A cache of the whole Nix store (`nix-community/cache-nix-action`) made the console job slower. The restore of 1.4 GB took 31 s and saved 12 s in the test step, so the jobs don't use it. The first devenv step of a job takes 25 to 33 s, because the shell is cold. Running the steps that need no tool outside devenv only moves that cost to the next step, so the `helm` job doesn't do it.

The `changes` job and the `helm` job run in the `release` profile (`nix/profiles/release.nix`), which has only the `ci` script, `cog`, Helm and Git. It has no C compiler. They don't evaluate the whole `repo` profile.

The workflow `.github/workflows/cache-cleanup.yaml` deletes the Actions caches of a branch when someone deletes the branch, and the caches of a pull request when it closes. The caches of `main` stay. This keeps the 10 GB limit free for the caches that the jobs use.

A failed lint or test job stops the build and the release. The end-to-end (e2e) suite doesn't run in CI. Run it on the local cluster with `cluster k6 e2e`.

Two more pages go into the detail. [CI images and releases](ci-images.md) describes the image tags, the pin step and the other `ci` commands. [CI security and settings](ci-security.md) describes the SAST scan, the secrets, the variables and the settings for another forge.

<!-- nav:start -->
---

[Previous: Chart releases](releases.md) | [Next: CI images and releases](ci-images.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
