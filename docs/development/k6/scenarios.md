# E2E scenarios

These sections describe what the end-to-end scenarios check and how to run the suite.

## Run the suite

The suite doesn't run in CI, because it needs the k3d cluster. It's the only test of roles and access. Run it before you merge a change to a permission, a role, an access policy, or a view.

`cluster k6 e2e` verifies that the existing release is ready. It clears persisted test state and waits for the primary, the standby, PgDog, and PostgREST. Then it runs the full suite. It doesn't rebuild images, and it doesn't force a CloudNativePG (CNPG) image rollout.

Use `cluster k6 e2e --build` after a change to an image, the sync configuration, fallback, SeaweedFS, or `pg_duckdb`. The build form creates a new immutable image tag, applies Helmfile, and then runs the suite.

The suite also checks the topology:

- A primary and a standby stream.
- PgDog is available.
- PostgREST reloaded without a restart.
- Reads reach a standby.
- The PostgREST listener sits on the primary.

## Prepare the cluster

The Keycloak test realm has one client for each role. Each client has a fixed `role` claim:

| Client | Role |
| --- | --- |
| `api`, `api_no_policy` | `dp_api` |
| `policy` | `dp_policy` |
| `console` | `dp_console` |
| `admin` | `dp_admin` |
| `admin_other` | `dp_admin` for another schema |

The `schemas` claim of `admin` lists the schemas that the console scenarios create.

Some scenarios need set-up that the older scenarios don't:

| Scenario | Needs |
| --- | --- |
| `tokens` | The Keycloak clients `expired` and `wrong_audience`. |
| `idp` | The `idp` client with its two users. |
| `roles` | The `role_*` and `schemas_*` clients. |
| `newschema` | The BigQuery table `new_schema_table`. Run `seed` once to create it. |

Keycloak imports its realm only at the first start. After a change to `k3d/values/keycloak.yaml`, or after a pull that changes the realm, run `cluster down` and `cluster up` again.

## Check the rules file

Run `chart:lint:toml` (`devenv --profile checks tasks run chart:lint:toml`) after a change in `rbac.toml`. It runs `taplo lint`, which checks the file against `rbac.schema.json`, and `taplo fmt --check`. The `taplo` language server in the `checks` profile runs the same checks in an editor.

## Scenarios

These scenarios check the lifecycle and the resilience of the system:

| Scenario | What it checks |
| --- | --- |
| `schedule` | The scheduler starts a run. |
| `runs` | A run from the console completes. |
| `newschema` | A new schema is served end to end, with the exact rows of its seeded table. Provision also drops what an editor removes: the schema of the database, the catalog schema, and the files in S3. |
| `tokens` | The gateway refuses an expired token and a token for another audience. |
| `idp` | A user of the `idp` client reads data and the console. The client has client roles and a user attribute, as the identity provider recipe says. |
| `standby` | Reads survive the loss of a standby. |
| `cachestore` | Reads survive the loss of Valkey. |
| `metrics` | PostgREST exports its pool metrics. |
| `failover` | A failover of the primary leaves the system working. |
| `jobs` | The scenario restores the latest backup into a scratch schema and compares it with the live access policy. |
| `cache` | A write to the access policy that skips the proxy makes a cached answer outdated after a reload. |

### The roles scenario

The `roles` scenario is a table. The image build converts `rbac.toml` to `/scripts/rbac.json` with `tomljson`. The scenario reads that file with `open()`, so the matrix follows `rbac.toml`.

For every role and every permission, one probe uses what the permission guards, with the real token of the role. The scenario expects the probe to succeed exactly when the closure of the rules contains the permission. When you change `rbac.toml`, no scenario needs a new row, but a new permission needs a probe.

The scenario also checks these points:

- Each token has its role.
- `console.permissions()` lists the closure, and in the cluster `rbac.roles` contains the same closure.
- A rerun of `configure-db` with other rules changes what a role may do immediately, also for a cached answer. The scenario reruns `configure-db` from its suspended CronJob.
- A token gets no rights if its role is a database login that isn't a request role (`ops`, `serving`, `postgres`). It also gets no rights for an unknown role or no role.
- The system handles a `schemas` claim in plain text, or an empty list.
- An unknown permission ends as an HTTP 500.

### The console scenario

The `console` scenario checks the API of the console. It does these things:

- It reads the overview, the runs, and the tasks of the schema.
- It proves that a token for another schema reads nothing.
- It starts a run as an admin.
- It changes and restores the cache lifetime of a table.
- It lists the Parquet files of a table.
- It checks that no role changes a queue or a schedule, because both are read-only.
- It calls every view and helper function at every level.
- It checks the status views against the synced schema and the blocked table.
- It adds and removes a schema as the admin.
- It checks that the API refuses a request without a token, or with the token of a data user.

<!-- nav:start -->
---

[Previous: E2E layout](e2e.md) | [Next: Operations](../../operations/index.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
