# Production deploys

Staging deploys every release. Production runs one chart version that a person approves.

## How the versions move

- The staging HelmRelease follows the range `2.x`, so Flux deploys each new chart within five minutes.
- The production HelmRelease follows the chart tag `prod` in `ghcr.io/prefeitura-rio/charts/data-proxy`. The tag points to one version, and production stays on it until the tag moves.

Staging runs 2.11.0, and then CI releases 2.12.0 and 2.13.0. Staging now runs 2.13.0, and production still runs 2.11.0. After an approval, production runs 2.13.0. A chart has every change before it, so 2.13.0 includes 2.12.0.

## Approve a deploy

The workflow `promote.yaml` runs after each CI run on `main`. It compares the newest chart with the version of the tag `prod`. When they differ, it starts the job `Promote to production` in the environment `production`, and the job waits for a reviewer. The job name shows the version that production runs and the version to promote.

1. Open the run in GitHub Actions and choose Review deployments.
2. Check that staging is healthy with the version that you promote.
3. Approve. The job moves the tag `prod` and writes `prod: 2.11.0 to 2.13.0` in its summary.
4. Flux deploys the chart to production within five minutes.

Each Promote run cancels every other Promote run that waits for review. At most one run waits, and it's the newest. An approval of an old run does nothing when a newer chart exists.

## Roll back or choose a version

Start `promote.yaml` by hand with the input `version`, such as `2.11.0`. The job needs the same approval. A rollback leaves staging alone. The next Promote run cancels a rollback that waits, even when production already runs the newest chart. Approve the rollback before the next merge, or start it again.

## Commands

| Command | Work |
| --- | --- |
| `ci chart prod` | Prints the version that production runs. |
| `ci chart next` | Writes the version to promote, the version of production and whether they differ. |
| `ci chart promote <version>` | Moves the tag `prod` to a released version. `--if-latest` skips an old version. |

## Settings

The reviewers are in the GitHub settings of the repository, under Environments and `production`. The infra repository has the production HelmRelease. It reads the tag with an `OCIRepository` whose `ref.tag` is `prod`. [CI and releases](./ci.md) describes the chart release that comes before a promotion.

<!-- nav:start -->
---

[Previous: CI and releases](./ci.md) | [Next: Provider releases](./provider.md) | [Index](./index.md) | [Home](../index.md)
<!-- nav:end -->
