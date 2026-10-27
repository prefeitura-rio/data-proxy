# Environment Variables

Application defaults apply outside Helm. Helm sets the same values through the chart templates. The Default column contains the default of the application. A row names the chart value where the chart sets another one. The worker doesn't print a password or a key. It stops at start if a required variable is missing.

## Connections

| Variable | Default | Meaning |
| --- | --- | --- |
| `PG_DATABASE_URL` | required | PostgreSQL data source name (DSN) for the target database. |
| `REDIS_READ` | `redis://localhost:6379/1` | Redis address for cache reads. |
| `REDIS_WRITE` | `redis://localhost:6379/0` | Redis address for cache writes. |
| `DBOS_SYSTEM_DATABASE_URL` | required | DBOS system database address. |
| `GOOGLE_APPLICATION_CREDENTIALS` | unset | BigQuery credentials file. Omit with Workload Identity. |

`schemas.<schema>.source` contains only non-secret source configuration. Put BigQuery and future source credentials in environment variables or Kubernetes Secrets.

## S3 and DuckLake

These variables set the object store and the DuckLake catalogs.

| Variable | Default | Meaning |
| --- | --- | --- |
| `S3_BUCKET` | `test-bucket` | SeaweedFS/S3 bucket for scratch and DuckLake data. |
| `S3_ENDPOINT` | `localhost:8333` | S3 endpoint host and port. |
| `S3_REGION` | `us-east-1` | S3 region name. |
| `S3_USE_SSL` | `false` | Use TLS for S3. |
| `S3_ACCESS_KEY` | required | S3 access key. |
| `S3_SECRET_KEY` | required | S3 secret key. |
| `S3_SCRATCH_PREFIX` | `tmp` | The folder of the temporary extraction Parquet files. The sync worker deletes everything under it when a run ends, so each install that shares a bucket needs its own. The chart sets it with `s3.scratchPrefix`. |
| `S3_IDENTITIES` | `false` | Delete the S3 identities of schemas that the console no longer lists (the step `cleanup_s3_credentials`). The chart turns it on with `console.storage.perSchemaIdentities`. |
| `DUCKLAKE_CATALOG_URL` | required | Connection URI of the catalog database, with the `ops` role. The chart builds it from the password Secret of `ops`. Each schema writes to its own metadata schema. |
| `DUCKLAKE_CATALOG_PATH` | `ducklake` | S3 prefix for the per-schema Parquet data. |
| `DUCKLAKE_TARGET_FILE_SIZE` | `512MB` | Target Parquet file size for DuckLake writes. |
| `DUCKLAKE_SNAPSHOT_EXPIRATION` | `7d` | Age at which old snapshots are expired and files become eligible for cleanup. |
| `DUCKLAKE_COMMIT_MAX_ATTEMPTS` | `60` | How many times the commit and the maintenance of a schema retry while the catalog is locked. |
| `DUCKLAKE_COMMIT_RETRY_SECONDS` | `1.0` | Seconds between two of those retries. |
| `DUCKLAKE_MAX_COMPACTED_FILES` | `10` | Maximum compacted files per table in one maintenance call. |
| `DUCKLAKE_REWRITE_DELETE_THRESHOLD` | `0.95` | Minimum deleted fraction for rewriting a data file. |

## DBOS

These variables set the DBOS application and its system database.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DBOS_APPLICATION_NAME` | `data-proxy-sync` | DBOS application name. |
| `DBOS_APPLICATION_VERSION` | `0.1.0` | DBOS application version. |
| `DBOS_SYSTEM_SCHEMA` | `dbos` | DBOS metadata schema. |
| `DBOS_APP_SCHEMA` | `data_proxy` | Application state schema. |

## Sync queues and schedule

These variables set the first values of the DBOS queues and of the sync schedule, and the limits of a sync run.

| Variable | Default | Meaning |
| --- | --- | --- |
| `SYNC_SCHEDULE` | `0 2 * * *` | The cron of the DBOS schedule that a new schema gets. The schedule is named `sync-<schema>`. The console changes the cron of a schedule later. |
| `SYNC_QUEUE_CONCURRENCY` | `1` | Initial sync workflow concurrency. |
| `LISTEN_QUEUES` | empty | Queue names, separated by commas, that this worker listens to. Empty means every queue. An unknown name stops the worker. |
| `SYNC_RUN_TIMEOUT_SECONDS` | `3600` | Maximum duration of one sync run. |
| `SYNC_STEP_MAX_ATTEMPTS` | `3` | Retry attempts for transient steps. |
| `DUMP_QUEUE_WORKER_CONCURRENCY` | `2` | Initial dump worker concurrency. |
| `DUCKDB_MEMORY_LIMIT` | `1GB` | Memory of each DuckDB connection of a sync worker. DuckDB spills to the disk beyond it. |
| `DUCKDB_THREADS` | `2` | Threads of each DuckDB connection. |
| `DUCKDB_TEMP_DIRECTORY` | `/tmp/duckdb` | Parent folder of the spill of the DuckDB connections. Each connection spills to a folder of its own in it, and removes the folder when it closes. |
| `DUMP_QUEUE_RATE_LIMIT` | `0` | Initial dump tasks per minute. `0` turns the limit off. |
| `DUMP_QUEUE_MAX_ATTEMPTS` | `3` | Dump workflow retry attempts. |

The engine creates the queues and the schedule when they don't exist, and never overwrites them. These values are only the first ones, because a restart keeps the stored values. The console shows the queues and the schedules and doesn't edit them. See [Control a run and a schedule](../console/runs.md) for what a schedule allows.

## Observability

These variables set the log level and the OpenTelemetry Protocol (OTLP) endpoints for the logs, the traces and the metrics of the sync workers.

| Variable | Default | Meaning |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | Log level of the sync workers: `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. |
| `OTLP_LOGS_ENDPOINT` | unset | OTLP logs endpoint. |
| `OTLP_TRACES_ENDPOINT` | unset | OTLP traces endpoint. |
| `OTLP_METRICS_ENDPOINT` | unset | OTLP metrics endpoint. |

See [Proxy variables](proxy.md) for the variables of the roles and of the OpenResty proxy.

<!-- nav:start -->
---

[Previous: Hardening](hardening.md) | [Next: Proxy variables](proxy.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
