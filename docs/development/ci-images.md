# CI images and releases

The pipeline builds the images, signs them and pins them in the chart. This page describes the image tags, the pin step and the other `ci` commands. [CI pipeline](ci.md) describes the jobs.

## Images

`ci changes` matches the changed files against the `change_pattern` of each image in the table `IMAGES` of `scripts/lib.nu`. The jobs build only the images that match.

| Image | Containerfile | Rebuilds when this path changes |
| --- | --- | --- |
| `data-proxy-sync` | `Containerfile.sync` | `sync/src/`, `sync/pyproject.toml`, `sync/uv.lock`, `console/migrations/` |
| `data-proxy-proxy` | `Containerfile.proxy` | `proxy/src/`, `proxy/lux.toml`, `proxy/lux.lock` |
| `data-proxy-jobs` | `Containerfile.jobs` | `Containerfile.jobs` |
| `data-proxy-postgres` | `Containerfile.pg` | `Containerfile.pg` |
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

<!-- nav:start -->
---

[Previous: CI pipeline](ci.md) | [Next: CI security and settings](ci-security.md) | [Index](ci.md) | [Home](../index.md)
<!-- nav:end -->
