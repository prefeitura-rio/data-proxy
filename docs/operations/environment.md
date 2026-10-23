# Environment Variables

Application defaults apply outside Helm. Helm sets the same values through the chart templates. The Default column contains the default of the application. A row names the chart value where the chart sets another one.

## Connections

| Variable | Default | Meaning |
| --- | --- | --- |
| `PG_DATABASE_URL` | `postgresql://test:test@localhost:5432/test` | PostgreSQL data source name (DSN) for the target database. |
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
| `S3_ACCESS_KEY` | `seaweedfs` | S3 access key. |
| `S3_SECRET_KEY` | `seaweedfs-local` | S3 secret key. |
| `S3_SCRATCH_PREFIX` | `tmp` | The folder of the temporary extraction Parquet files. The sync worker deletes everything under it when a run ends, so each install that shares a bucket needs its own. The chart sets it with `s3.scratchPrefix`. |
| `S3_IDENTITIES` | `false` | Delete the S3 identities of schemas that the console no longer lists (the step `cleanup_s3_credentials`). The chart turns it on with `console.storage.perSchemaIdentities`. |
| `DUCKLAKE_CATALOG_URL` | `postgresql://test:test@localhost:5432/ducklake` | Connection URI of the catalog database, with the `ops` role. The chart builds it from the password Secret of `ops`. Each schema writes to its own metadata schema. |
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
| `SYNC_SCHEDULE` | `0 2 * * *` | Initial DBOS schedule for `run_sync`. |
| `SYNC_SCHEDULE_NAME` | `sync` | DBOS schedule name. |
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

## Auth and proxy

These variables set the PostgreSQL roles of the serving layer and the settings that connect the sync workers to the proxy and the provision job.

| Variable | Default | Meaning |
| --- | --- | --- |
| `AUTH_ANON_ROLE` | `anon` | Unauthenticated PostgreSQL role. |
| `AUTH_USER_ROLE` | `dp_api` | The baseline PostgreSQL role. Every role of `rbac.toml` is a member of it. The chart sets it with `auth.userRole`. |
| `AUTH_SERVING_ROLE` | `serving` | The login of the serving layer: PostgREST, PgDog and the recovery Deployment. |
| `PROXY_CACHE_REDIS_DB` | `1` | Redis database for cached proxy responses. |
| `PROXY_ADMIN_URL` | unset | Address of the reload endpoint of the proxy, for example `http://data-proxy-proxy-admin:8081/reload`. The sync workers resolve its host to every proxy pod and send each one a reload request. Unset: nobody is notified. The chart sets it. |
| `PROVISION_CRONJOB` | unset | Name of the suspended `provision` CronJob. A sync worker makes a Job from it when a schema changes and at the start of every sync run. The chart sets it. Unset, the worker skips the step. |
| `PROVISION_TIMEOUT_SECONDS` | `600` | How long the worker waits for the provision Job. |

## OpenResty proxy

The Helm chart sets these variables on the proxy Deployment. The Lua code reads them through `config.load` in `proxy/src/proxy/config.lua`. The proxy loads all settings once at start-up.

It collects the text of the variables in one table. Then it converts and checks that table once with the `config` schema (`proxy/src/proxy/schemas/definitions/config.lua`). A variable that isn't set uses the default of its setting.

A missing required variable, a value of the wrong type, or a value outside the limits stops nginx from starting. The error names the setting. A variable name is `PROXY_` and the upper-case name of the setting. `helm/files/config/nginx.conf` lists every variable with an `env` directive, because nginx removes the other variables.

| Variable | Default | Meaning |
| --- | --- | --- |
| `PROXY_CACHE_TTL` | `300` | Default cache lifetime in seconds, `0` or more. A table in `sync.json` can set its own `cache_ttl`. |
| `PROXY_MAX_BODY` | `262144` | Largest response body in bytes that the proxy stores. `0` means no limit. |
| `PROXY_REDIS_HOST` | required | Redis host for the cache. The operator provides one stable endpoint. |
| `PROXY_REDIS_PORT` | `6379` | Redis port for the cache, from `1` to `65535`. |
| `PROXY_REDIS_DATABASE` | `1` | Redis database for cached responses, `0` or more. |
| `PROXY_REDIS_PASSWORD` | unset | Redis password. Omit it for no login. |
| `PROXY_POSTGREST` | required | Base address of the PostgREST service. |
| `PROXY_MAX_INFLIGHT` | `0` | Most requests that wait for PostgREST at the same time in one proxy pod. More get a `503` with `Retry-After`. `0` means no limit. The chart sets `32`. |
| `PROXY_CONFIG_URL` | unset | Address of the configuration function of PostgREST, for example `http://postgrest:3000/rpc/config`. The proxy sends the profile `console` with it. When it's set, the proxy takes its table settings from there. The chart sets it. |
| `PROXY_CONFIG_REFRESH_SECONDS` | `60` | Seconds between two checks of PostgREST for a new document. The check is a safety net: the sync workers also call the reload endpoint immediately after a change. |

## Observability

These variables set the log level and the OpenTelemetry Protocol (OTLP) endpoints for the logs, the traces and the metrics of the sync workers.

| Variable | Default | Meaning |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | Log level of the sync workers: `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. |
| `OTLP_LOGS_ENDPOINT` | unset | OTLP logs endpoint. |
| `OTLP_TRACES_ENDPOINT` | unset | OTLP traces endpoint. |
| `OTLP_METRICS_ENDPOINT` | unset | OTLP metrics endpoint. |

---

<!-- nav:start -->
---

[Previous: Topology](topology.md) | [Next: KEDA Scaling](keda.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
