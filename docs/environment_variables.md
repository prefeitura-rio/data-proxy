# Environment Variables

Application defaults apply outside Helm. Helm sets the same values through the chart templates.

## Connections

| Variable | Default | Meaning |
| --- | --- | --- |
| `PG_DATABASE_URL` | `postgresql://test:test@localhost:5432/test` | PostgreSQL DSN for the target database. |
| `REDIS_READ` | `redis://localhost:6379/1` | Redis address for cache reads. |
| `REDIS_WRITE` | `redis://localhost:6379/0` | Redis address for cache writes. |
| `DBOS_SYSTEM_DATABASE_URL` | required | DBOS system database address. |
| `SYNC_CONFIG_URL` | unset | Address of the `sync.json` that the console serves, for example `http://console:8000/config/sync.json`. Each sync run loads it once, at its start. |
| `SYNC_CONFIG_PATH` | `config/sync.json` | Sync configuration file. The engine reads it when `SYNC_CONFIG_URL` is unset. |
| `GOOGLE_APPLICATION_CREDENTIALS` | unset | BigQuery credentials file. Omit with Workload Identity. |

`schemas.<schema>.source` contains only non-secret source configuration. Put BigQuery and future source credentials in environment variables or Kubernetes Secrets.

## Console

The console reads two databases. Both addresses should point at a PgBouncer pooler in **session** mode. Session mode keeps prepared statements and `LISTEN/NOTIFY` working, which DBOS uses to wait for log lines.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DBOS_SYSTEM_DATABASE_URL` | required | DBOS system database. Use the read/write pooler, because the console enqueues, cancels and updates workflows. |
| `DBOS_SYSTEM_SCHEMA` | `dbos` | Schema of the DBOS tables. |
| `PG_DATABASE_URL` | required | Application database. The console writes the configuration revisions there, so it needs the read/write pooler. |
| `STREAM_INTERVAL_SECONDS` | `2.0` | Seconds between two polls of a monitoring stream. |

Each pooled connection holds one server connection for its whole life. One console replica opens up to 10: the DBOS client uses at most 5 plus one for notifications, and the database engine of the console opens up to 4 on demand. Keep this in mind when you size `cnpg.pooler.poolSize`. Transaction mode isn't supported: it breaks `LISTEN/NOTIFY`.

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
| `READER_REFRESH_CONCURRENCY` | `4` | Maximum catalog refresh Jobs that run at once (the size of the `refresh` queue). |
| `READER_REFRESH_ATTEMPTS` | `5` | How many times the refresh Jobs run for the schemas whose snapshot the primary doesn't report yet. |
| `READER_REFRESH_RETRY_SECONDS` | `5` | Seconds to wait before the refresh Jobs run again. |
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

## OpenResty proxy

The Helm chart sets these variables on the proxy Deployment. The Lua code reads them through `config.load` in `proxy/src/proxy/config.lua`. The proxy loads all settings once at start-up. It collects the text of the variables in one table, and converts and checks the table once with the `config` schema (`proxy/src/proxy/schemas/config.lua`). A variable that isn't set gives the default of its setting. A missing required variable, a value of the wrong type, or a value outside the limits stops nginx from starting, with an error that names the setting. The variable is `PROXY_` and the upper-case name of the setting. `helm/files/nginx.conf` lists every variable with an `env` directive, because nginx removes the other variables.

| Variable | Default | Meaning |
| --- | --- | --- |
| `PROXY_CACHE_TTL` | `300` | Default cache lifetime in seconds, `0` or more. A table in `sync.json` can set its own `cache_ttl`. |
| `PROXY_MAX_BODY` | `262144` | Largest response body in bytes that the proxy stores. `0` means no limit. |
| `PROXY_HA_MODE` | `false` | `true` if reads can retry on the write PostgREST. |
| `PROXY_REDIS_HOST` | required | Redis host for the cache. The operator gives one stable endpoint. |
| `PROXY_REDIS_PORT` | `6379` | Redis port for the cache, from `1` to `65535`. |
| `PROXY_REDIS_DATABASE` | `1` | Redis database for cached responses, `0` or more. |
| `PROXY_REDIS_PASSWORD` | unset | Redis password. Omit it for no login. |
| `PROXY_POSTGREST_READ` | required | Base address of the PostgREST service for reads. |
| `PROXY_POSTGREST_WRITE` | required | Base address of the PostgREST service for writes. |
| `PROXY_POSTGREST_RETRIES` | `1` | Retry attempts on the retry service after the first attempt, from `0` to `10`. |
| `PROXY_POSTGREST_BACKOFF_MS` | `0` | Delay before the first retry, in milliseconds, from `0` to `60000`. Each retry doubles it. |
| `PROXY_MAX_INFLIGHT` | `0` | Most requests that wait for PostgREST at the same time in one proxy pod. More get a `503` with `Retry-After`. `0` means no limit. The chart sets `32`. |

## PostgREST rollout

| Variable | Default | Meaning |
| --- | --- | --- |
| `KUBERNETES_NAMESPACE` | `data-proxy` | Kubernetes namespace for conditional rollout. |
| `DEPLOYMENT_ROLLOUT_TIMEOUT_SECONDS` | `300` | Pooler and PostgREST rollout timeout. |

The workflow discovers Pooler and PostgREST Deployments by label. After a published snapshot, all Pooler Deployments restart together, then all PostgREST Deployments restart together. PostgREST also rolls out when seed adds or removes views.

## Observability

| Variable | Default | Meaning |
| --- | --- | --- |
| `OTLP_LOGS_ENDPOINT` | unset | OTLP logs endpoint. |
| `OTLP_TRACES_ENDPOINT` | unset | OTLP traces endpoint. |
| `OTLP_METRICS_ENDPOINT` | unset | OTLP metrics endpoint. |

---

[← Previous](database.md) · [Home](../README.md) · [Next →](helm_chart.md)
