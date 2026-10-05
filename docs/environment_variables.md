# Environment Variables

Application defaults apply outside Helm. Helm sets the same values through the chart templates.

## Connections

| Variable | Default | Meaning |
| --- | --- | --- |
| `PG_DATABASE_URL` | `postgresql://test:test@localhost:5432/test` | PostgreSQL DSN for the target database. |
| `REDIS_READ` | `redis://localhost:6379/1` | Redis address for cache reads. |
| `REDIS_WRITE` | `redis://localhost:6379/0` | Redis address for cache writes. |
| `DBOS_SYSTEM_DATABASE_URL` | required | DBOS system database address. |
| `SYNC_CONFIG_PATH` | `config/sync.json` | Sync configuration path. |
| `GOOGLE_APPLICATION_CREDENTIALS` | unset | BigQuery credentials file. Omit with Workload Identity. |

`schemas.<schema>.source` contains only non-secret source configuration. Put BigQuery and future source credentials in environment variables or Kubernetes Secrets.

## S3 and DuckLake

| Variable | Default | Meaning |
| --- | --- | --- |
| `S3_BUCKET` | `test-bucket` | SeaweedFS/S3 bucket for scratch and DuckLake data. |
| `S3_ENDPOINT` | `localhost:8333` | S3 endpoint host and port. |
| `S3_REGION` | `us-east-1` | S3 region name. |
| `S3_USE_SSL` | `false` | Use TLS for S3. |
| `S3_ACCESS_KEY` | `seaweedfs` | S3 access key. |
| `S3_SECRET_KEY` | `seaweedfs` | S3 secret key. |
| `S3_SCRATCH_PREFIX` | `tmp` | Temporary extraction Parquet prefix. |
| `DUCKLAKE_CATALOG_LOCAL_PATH` | `/var/lib/ducklake/catalogs` | Local root for schema SQLite catalogs. |
| `DUCKLAKE_CATALOG_PATH` | `ducklake` | S3 prefix for per-schema SQLite catalogs and Litestream replicas. |
| `DUCKLAKE_TARGET_FILE_SIZE` | `512MB` | Target Parquet file size for DuckLake writes. |
| `DUCKLAKE_SNAPSHOT_EXPIRATION` | `7d` | Age at which old snapshots are expired and files become eligible for cleanup. |
| `DUCKLAKE_MAX_COMPACTED_FILES` | `10` | Maximum compacted files per table in one maintenance call. |
| `DUCKLAKE_REWRITE_DELETE_THRESHOLD` | `0.95` | Minimum deleted fraction for rewriting a data file. |

## DBOS

| Variable | Default | Meaning |
| --- | --- | --- |
| `DBOS_APPLICATION_NAME` | `data-proxy-sync` | DBOS application name. |
| `DBOS_APPLICATION_VERSION` | `0.1.0` | DBOS application version. |
| `DBOS_SYSTEM_SCHEMA` | `dbos` | DBOS metadata schema. |
| `DBOS_APP_SCHEMA` | `data_proxy` | Application state schema. |

## Sync queues and schedule

| Variable | Default | Meaning |
| --- | --- | --- |
| `SYNC_SCHEDULE` | `0 2 * * *` | DBOS schedule for `run_sync`. |
| `SYNC_SCHEDULE_NAME` | `sync` | DBOS schedule name. |
| `SYNC_QUEUE_CONCURRENCY` | `1` | Sync workflow concurrency. |
| `SYNC_RUN_TIMEOUT_SECONDS` | `3600` | Maximum duration of one sync run. |
| `SYNC_STEP_MAX_ATTEMPTS` | `3` | Retry attempts for transient steps. |
| `READER_SNAPSHOT_TIMEOUT_SECONDS` | `120` | How long a publish waits for the reader catalog to apply the committed snapshot. |
| `READER_SNAPSHOT_POLL_SECONDS` | `1` | Poll interval while a publish waits for the reader catalog. |
| `DUMP_QUEUE_WORKER_CONCURRENCY` | `4` | Dump worker concurrency. |
| `DUMP_QUEUE_RATE_LIMIT` | `50` | Dump tasks per minute. |

## Dumper

| Variable | Default | Meaning |
| --- | --- | --- |
| `DUMPER_BATCH_BYTES` | `629145600` | Uncompressed batch target in bytes. |
| `DUMPER_BATCH_MAX_PARTITIONS` | `256` | Maximum partitions in one task. |
| `DUMPER_SCRATCH_DIR` | system temporary directory | Local extraction scratch directory. |
| `DUMP_QUEUE_MAX_ATTEMPTS` | `3` | Dump workflow retry attempts. |

## Auth and proxy

| Variable | Default | Meaning |
| --- | --- | --- |
| `AUTH_ANON_ROLE` | `anon` | Unauthenticated PostgreSQL role. |
| `AUTH_USER_ROLE` | `user` | Authenticated PostgreSQL role. |
| `AUTH_AUTHENTICATOR_ROLE` | `authenticator` | PostgREST login role. |
| `PROXY_CACHE_REDIS_DB` | `1` | Redis database for cached proxy responses. |

## PostgREST rollout

| Variable | Default | Meaning |
| --- | --- | --- |
| `KUBERNETES_NAMESPACE` | `data-proxy` | Kubernetes namespace for conditional rollout. |
| `POSTGREST_RO_ROLLOUT_TIMEOUT_SECONDS` | `300` | Read PostgREST rollout timeout. |

PostgREST rolls out when seed adds or removes views. Data-only DuckLake snapshots do not trigger a rollout.

## Observability

| Variable | Default | Meaning |
| --- | --- | --- |
| `OTLP_LOGS_ENDPOINT` | unset | OTLP logs endpoint. |
| `OTLP_TRACES_ENDPOINT` | unset | OTLP traces endpoint. |
| `OTLP_METRICS_ENDPOINT` | unset | OTLP metrics endpoint. |

---

[← Previous](database.md) · [Home](../README.md) · [Next →](helm_chart.md)
