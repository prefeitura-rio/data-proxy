# Environment Variables

Application defaults below apply outside Helm. Helm can override them.

## Connections

| Variable | Application default | Meaning |
| --------------------------------- | ----------------------------------------------------------------- | ----------- |
| `PG_DATABASE_URL` | `postgresql://test:test@localhost:5432/test` | PostgreSQL DSN for the target database. |
| `REDIS_READ` | `redis://localhost:6379/1` | Redis URL for read operations (cache, fallback). |
| `REDIS_WRITE` | `redis://localhost:6379/0` | Redis URL for write operations. |
| `DBOS_SYSTEM_DATABASE_URL` | — (required) | DBOS system database URL. Holds workflow state and the application state schema. |
| `SCHEMA_WRITERS` | — (required, JSON) | JSON object mapping PostgreSQL schema names to writer DSNs. Loaded from a Secret in Helm. |
| `SYNC_CONFIG_PATH` | `config/sync.json` | Path to the sync configuration file. |
| `GOOGLE_APPLICATION_CREDENTIALS` | — | BigQuery service-account file. Omit with Workload Identity. |

## S3

| Variable | Application default | Meaning |
| --------------------------------- | ----------------------------------------------------------------- | ----------- |
| `S3_BUCKET` | `test-bucket` | Parquet bucket. |
| `S3_ENDPOINT` | `localhost:8333` | S3 endpoint host and port. |
| `S3_USE_SSL` | `false` | Use TLS for S3. |
| `S3_ACCESS_KEY` | `seaweedfs` | S3 access key. |
| `S3_SECRET_KEY` | `seaweedfs` | S3 secret key. |

## Dumper

| Variable | Application default | Meaning |
| --------------------------------- | ----------------------------------------------------------------- | ----------- |
| `DUMPER_BATCH_BYTES` | `629145600` | Uncompressed batch target in bytes. |
| `DUMPER_BATCH_MAX_PARTITIONS` | `256` | Maximum partitions in one dump task. |
| `DUMPER_SCRATCH_DIR` | System temporary directory | Local Parquet merge directory. |
| `DUMP_QUEUE_MAX_ATTEMPTS` | `3` | DBOS step retry attempts for one dump task. |

## DBOS

| Variable | Application default | Meaning |
| --------------------------------- | ----------------------------------------------------------------- | ----------- |
| `DBOS_APPLICATION_NAME` | `data-proxy-pipeline` | DBOS application name. |
| `DBOS_APPLICATION_VERSION` | `0.1.0` | DBOS application version. |
| `DBOS_SYSTEM_SCHEMA` | `dbos` | Postgres schema for DBOS system tables. |
| `DBOS_APP_SCHEMA` | `data_proxy` | Postgres schema for table state and errors. |

## Sync queues and schedule

| Variable | Application default | Meaning |
| --------------------------------- | ----------------------------------------------------------------- | ----------- |
| `SYNC_SCHEDULE` | `0 2 * * *` | Cron schedule for the DBOS `run_sync` workflow. |
| `SYNC_SCHEDULE_NAME` | `sync` | DBOS schedule name. |
| `SYNC_QUEUE_CONCURRENCY` | `1` | Per-process DBOS sync queue concurrency. |
| `SYNC_RUN_TIMEOUT_SECONDS` | `3600` | Maximum duration of one sync run. |
| `SYNC_STEP_MAX_ATTEMPTS` | `3` | Maximum attempts for safe transient DBOS steps. |
| `DUMP_QUEUE_WORKER_CONCURRENCY` | `4` | Per-process DBOS dump queue concurrency. |
| `DUMP_QUEUE_RATE_LIMIT` | `50` | Maximum dump tasks enqueued per 60 seconds. |
| `PUBLISH_QUEUE_WORKER_CONCURRENCY` | `4` | Per-process DBOS publish queue concurrency. |

## Auth

| Variable | Application default | Meaning |
| --------------------------------- | ----------------------------------------------------------------- | ----------- |
| `AUTH_ANON_ROLE` | `anon` | Unauthenticated PostgreSQL role. |
| `AUTH_USER_ROLE` | `user` | Authenticated PostgreSQL role. |
| `AUTH_AUTHENTICATOR_ROLE` | `authenticator` | PostgREST login role. |

## Fallback

| Variable | Application default | Meaning |
| --------------------------------- | ----------------------------------------------------------------- | ----------- |
| `FALLBACK_CACHE_REDIS_DB` | `1` | Redis database for fallback cache. |

## PostgREST and replication

| Variable | Application default | Meaning |
| --------------------------------- | ----------------------------------------------------------------- | ----------- |
| `KUBERNETES_NAMESPACE` | `data-proxy` | Kubernetes namespace for PostgREST rollout refresh. |
| `POSTGREST_RO_DEPLOYMENT_TEMPLATE` | `data-proxy-{}-postgrest-ro` | Read-only Deployment name template (`{}` = schema). |
| `POSTGREST_RW_DEPLOYMENT_TEMPLATE` | `data-proxy-{}-postgrest-rw` | Read-write Deployment name template (`{}` = schema). |
| `POSTGREST_RO_ROLLOUT_TIMEOUT_SECONDS` | `300` | Timeout for PostgREST read-only rollout refresh. |
| `REPLICATION_WAIT_TIMEOUT_SECONDS` | `300` | Timeout for replica WAL replay. |
| `REPLICATION_POLL_INTERVAL_SECONDS` | `1` | Poll interval for replica WAL replay. |

## Observability

| Variable | Application default | Meaning |
| --------------------------------- | ----------------------------------------------------------------- | ----------- |
| `OTLP_LOGS_ENDPOINT` | `""` (disabled) | OTLP logs endpoint. When empty, OTLP export is disabled. |
| `OTLP_TRACES_ENDPOINT` | `""` (disabled) | OTLP traces endpoint. When empty, trace export is disabled. |
| `OTLP_METRICS_ENDPOINT` | `""` (disabled) | OTLP metrics endpoint. When empty, metric export is disabled. |

The FastStream and Redis-stream variables (`PRODUCER_POLL_INTERVAL_SECONDS`, `DUMPER_MAX_RETRIES`, `DUMPER_VISIBILITY_TIMEOUT_MS`, `SEEDER_VISIBILITY_TIMEOUT_MS`, `PUBLISHER_VISIBILITY_TIMEOUT_MS`) are removed. DBOS owns run state, retries, and recovery. The Prometheus Pushgateway (`PUSHGATEWAY_URL`) is removed; observability uses one OTLP pipeline.

## Helm conversion

Helm accepts the batch target as MiB:

```yaml
sync:
  dumper:
    batchMegaBytes: 600
```

The chart converts this value to `DUMPER_BATCH_BYTES` by multiplying it by 1048576.

## Fallback

Configure nginx fallback behavior with Helm values under `fallback`. See [Fallback](fallback.md) for request and cache behavior.

---

[← Previous](database.md) · [Home](../README.md) · [Next →](helm_chart.md)
