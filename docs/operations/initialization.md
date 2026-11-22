# Initialization

The chart sets up the database storage, runs the initialization Jobs and waits until the database is ready.

## Database storage

CloudNativePG (CNPG) creates one retained PostgreSQL PersistentVolumeClaim (PVC) per instance. Configure the size under `cnpg.storage.size`. Kubernetes doesn't support PVC size reduction. Remove retained PVCs only as a separate destructive operation.

SeaweedFS stores Parquet data in its own configured storage. The PostgreSQL database uses pg_duckdb to read Parquet. pg_duckdb is an extension, not a separate database service.

## Database initialization and readiness

Three Jobs prepare the database, and each one waits for what it needs. A migration contains what changes only when a developer changes it. A Job contains what depends on a secret, on `rbac.toml`, on the schemas of the console or on a sync run.

```mermaid
{{#include ../diagrams/install-jobs-sequence.mmd}}
```

| Job | Waits for | Work |
| --- | --- | --- |
| `configure-db` | Its CNPG writer, and then the `rbac` schema of the migrations. | Runs idempotent SQL with `ON_ERROR_STOP=1`, as `ops`. CNPG owns two roles, `ops` and `duckdb`. The Job creates the request roles first. After the migrations, it writes the persistent DuckDB secrets, which only a superuser may write, loads the rules of `rbac.toml` and installs the recovery procedure. |
| `migrate-db` | The request roles, because its grants name them. | Runs in the sync image. It runs `dbos migrate`, then `goose up` on the application database with `migrations/console/` and on the catalog database with `migrations/ducklake/`. See below. |
| `provision` | The migrations of both databases, and the rules. | Runs on every install and upgrade. See below. |

The migrations make what changes only with the code:

- the extensions `postgis`, `pg_duckdb` and `ltree`
- the `data_proxy` schema: the state tables, the reload functions, the pre-request function and the maintenance procedures
- the `rbac` schema with its functions, and the `console` schema with its tables, views, functions and triggers
- the grants to the request roles
- in the catalog database, the table of the sealed keys and the privileges of the catalog reader

`configure-db` keeps what depends on a secret or on `rbac.toml`: the request roles, the rules and the DuckDB secrets. It also keeps the recovery procedure, which takes the application name from the chart.

Goose records each migration in `public.goose_db_version` of its database. Between `-- +goose ENVSUB ON` and `-- +goose ENVSUB OFF`, it fills the role names, such as `${AUTH_USER_ROLE}`, from the Job. Every name is quoted, so a name that isn't set stops the run.

`provision` also exists as a suspended CronJob, `<release>-provision`, that a Job comes from when a schema changes (see [Run a Job on demand](initialization.md#run-a-job-on-demand)). On install and upgrade, it does these steps:

- It fills an empty configuration once from `sync.config`. Afterward the console controls the configuration.
- For each configured schema, it creates the catalog reader role before the application schema. The application schema marks completed creation, so another run can detect unfinished work.
- It sets `pgrst.db_schemas` on the `serving` login to the data schemas, then `console` and `access`. The first schema is the default PostgREST profile.
- It reloads PostgREST only when the list changes and restores the previous list if the reload fails.

The database waits until every standby replays the change, then sends a `NOTIFY` on the `pgrst` channel.

Before reconciliation, `provision` renames legacy catalog schemas and reader roles to their physical names. It rereads configuration for up to three passes to handle edits during reconciliation. The initial import uses the administrator role for one transaction, because the Job has no end-user token. The migrations create the shared `access` schema and its policies.

PostgREST serves a schema that an editor adds within seconds. The job removes obsolete schema files first, then the catalog and its reader role, then the application schema. The application schema marks completed removal, so another run can detect unfinished work. See [Console](../console/configuration.md#remove-a-table-or-a-schema).

### Roll back

The Job never runs a Down part, and the baseline has none. To go back, deploy the previous chart version. To undo a later file with a Down part, run `goose down` by hand.

## Run a Job on demand

`configure-db` and `provision` each exist twice. One is a Job named by the release revision, which Helm runs on every install and upgrade. The other is a suspended CronJob, `<release>-configure-db` and `<release>-provision`.

A suspended CronJob never runs on a clock. It contains the pod definition, and both objects get it from one helper, so they can't differ. Something that needs the work creates a Job from the CronJob:

| Context | Who creates the Job | What runs |
| --- | --- | --- |
| Install and upgrade | Helm | The per-revision Jobs, with every schema (`PROVISION_ALL=true`). |
| An editor adds or removes a schema in the console | The sync worker. A trigger on `console.schemas` queues the workflow `provision_schemas`. | `provision`: what's pending and what's removed. |
| The start of every sync run | The sync worker, with the same call | `provision`. A lost event or a drift heals here. |
| An operator, after a change of `rbac.toml` or a failed run | A person | `configure-db` or `provision`. |

The one-line command for a person:

```bash
kubectl -n data-proxy create job --from=cronjob/<release>-provision provision-by-hand
kubectl -n data-proxy create job --from=cronjob/<release>-configure-db configure-db-by-hand
```

The sync ServiceAccount may read the CronJobs and create Jobs in its namespace, and nothing more.

The PostgREST Deployment uses an HTTP readiness probe on `/`. It isn't Ready until PostgREST is serving its configured schema.
