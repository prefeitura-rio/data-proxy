# CI security and settings

This page describes the SAST scan, the secrets and the variables of the workflows, and the settings for another forge. [CI pipeline](ci.md) describes the jobs.

## SAST

`sast.yaml` runs on a push to `main`, on a pull request to `main`, and on a manual start. The job calls the shared action `prefeitura-rio/actions/sast`. The action sends the code to SonarQube and the findings to DefectDojo.

`sonar-project.properties` excludes `scripts/`, `k3d/`, `k6/`, `helm/ci/` and `helm/tests/` from the analysis. They contain tooling and tests.

The run on `main` fails at the SonarQube quality gate, and the scanner exits with code 3. The log doesn't name the failed condition, so read the dashboard of the project on the SonarQube server. The log also shows these facts:

- Checkov reports five `CKV_SECRET_6` findings, although `.checkov.yaml` skips that check. The action may not read the file.
- Checkov can't render the chart, because `redis.host` has no default value. It then scans the chart as plain files.
- DefectDojo answered once with a server error when the action sent the findings again.

The repository doesn't cause these failures, so no file of `.github` changes for them.

## Branch protection

The branch `main` has no branch protection and no rule set. `gh api repos/prefeitura-rio/data-proxy/branches/main/protection` answers 404, and the list of rule sets is empty. A required check that names a job of `ci.yaml` needs the current job names: `Lint and unit tests`, `Sync integration tests`, `Console integration tests` and `Proxy integration tests`.

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

[Previous: CI images and releases](ci-images.md) | [Next: Seed BigQuery data](seed.md) | [Index](ci.md) | [Home](../index.md)
<!-- nav:end -->
