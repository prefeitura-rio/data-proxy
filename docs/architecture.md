# Architecture

## Serving Layer

The product is a PostgreSQL database that uses the pg\_duckdb extension. This database mirrors a set of BigQuery tables. PostgREST serves this data as a REST API.

BigQuery is the source of truth. pg\_duckdb is a read cache. Clients query pg\_duckdb over HTTP. Clients never query BigQuery directly.

pg\_duckdb embeds DuckDB's columnar engine inside PostgreSQL. This lets the finalizer read Parquet files straight from GCS. The finalizer loads these files into native PostgreSQL tables in one process. Data Proxy needs no separate ETL engine for this step. Everything downstream of the load stays ordinary PostgreSQL. PostgREST, row-level security, and roles all work as they would against any other PostgreSQL database.

The read path does not enable DuckDB execution (`duckdb.force_execution`). PostgREST's read workload is small: filtered, index-driven lookups. DuckDB's columnar engine accelerates large scans and aggregations instead. Routing reads through DuckDB gives no benefit here.

The `pre_request` function mirrors every JWT claim into a PostgreSQL session variable. Row-level security policies compare the configured identity claim against grants in the local `<schema>.access_policy` table. See [Security](security.md) for details.

The sync pipeline below keeps pg\_duckdb up to date with BigQuery. This pipeline runs outside the request path.

## Data sync

The sync pipeline has three components. Each component writes one JSON log record per line. Search logs with `component`, `sync_id`, `table`, or `stage`.

- **Producer** — runs as a Kubernetes CronJob. It creates the worker and finalizer consumer groups. It reads the sync configuration. It compares each BigQuery table signature with the last successful signature. It publishes tasks only for changed tables. It writes one sync plan to Valkey. The plan contains a list of publication plans, one for each affected PostgreSQL schema. The pod exits after it publishes the plan and tasks.
- **Worker** — runs as a KEDA ScaledJob. KEDA uses two triggers on the `dp:sync:tasks` stream. `lagCount` counts unread messages. `pendingEntriesCount` counts messages that a worker received but did not acknowledge. KEDA runs a maximum of `maxReplicaCount` pods. Each pod processes one table or partition task. It writes one Parquet file to Google Cloud Storage, records the result in Valkey, and exits. One subscription reads new messages. The other subscription reclaims pending messages after `WORKER_VISIBILITY_TIMEOUT_MS`. The number of pods decreases to zero between sync runs.
- **Finalizer** — runs as a KEDA ScaledJob. Only one finalizer pod runs at a time. It reads each schema plan from the stored sync plan. It uses the configured writer for that schema. It loads only the Parquet paths in the schema plan. It publishes changed tables and commits successful signatures and partition manifests. It then tells PostgREST to reload its schema cache. A second subscription reclaims pending messages after `FINALIZER_VISIBILITY_TIMEOUT_MS`.

The producer skips a BigQuery table that has not changed since its last successful sync. The producer checks this with a modification signature. This signature combines the BigQuery modification time with the table's synchronization configuration. A configuration change therefore also forces a resync.

A table's strategy sets how many tasks the producer publishes for it. The `full` strategy publishes one task for the whole table. The `partitioned` strategy publishes one task per changed physical partition. See [Sync Configuration](sync.md) for the full reference.

A finalizer crash leaves its message pending. A new finalizer pod reclaims the message after the visibility timeout. It then completes the run. The producer re-publishes a finalizer message when all tasks are complete and no finalizer message is pending. This recovers a message that no finalizer received.

The worker records the path of each failed extraction task. The finalizer publishes the successful parts of an incremental partition update. It keeps old data for a failed existing partition. It does not add data for a failed new partition. The committed manifest describes the data that PostgreSQL serves. As a result, the next producer run schedules each failed partition again.

A full table is atomic. A partitioned full rebuild is also atomic. One extraction failure blocks publication of the complete table. A preparation failure also blocks state commit for that table. A publication failure has the same effect.

Each configured schema has a `freshness` table. This table gives the last publication time. It also gives the result of the latest attempt. The finalizer updates freshness in the same transaction as the data-table swap.

A loss of Valkey state causes a full resync. This is safe. BigQuery stays the source of truth at all times.

## Modes

### Standalone

Use standalone mode for development. Use standalone mode for single-region deployments.

```mermaid
flowchart TD
    BQ[(BigQuery)]
    R[(Valkey\nStreams)]
    GCS[(GCS\nParquet)]
    DB[(pgduckdb)]

    subgraph pipeline[Sync pipeline]
        P[Producer\nCronJob] --> R --> W[Worker\nScaledJob]
        W --> GCS --> FIN[Finalizer\nScaledJob]
    end

    BQ -->|discover partitions| P
    FIN -->|COPY INTO| DB
    DB -->|read| PGRST[PostgREST]
    PGRST -->|write access_policy| DB
    PGRST -->|REST + JWT| Client([API Client])
```

### High Availability

HA mode creates one independent Patroni and HAProxy stack for each configured application schema. Patroni uses the Kubernetes API as its distributed configuration store (DCS). HAProxy port `5000` sends PostgreSQL connections to the current primary. HAProxy port `5001` sends connections to replicas.

Publication is atomic inside one schema database. It is not atomic across independent schema databases. If one schema publishes and a later schema fails, the finalizer does not commit synchronization state. A retry can publish an already-published schema again. Publication operations must remain idempotent.

```mermaid
flowchart TD
    BQ[(BigQuery)]
    R[(Valkey\nStreams)]
    GCS[(GCS\nParquet)]
    API[Istio\nVirtualService]
    Client([API Client])

    subgraph pipeline[Shared sync pipeline]
        P[Producer\nCronJob] --> R --> W[Worker\nScaledJob]
        W --> GCS --> FIN[Finalizer\nScaledJob]
    end

    subgraph cadastro[bcadastro schema stack]
        direction TB
        PRW1[PostgREST RW] --> H1[HAProxy :5000]
        PRO1[PostgREST RO] --> H1R[HAProxy :5001]
        H1 --> PG1[(Patroni primary)]
        PG1 -->|WAL| PG2[(Patroni replica)]
        H1R --> PG2
    end

    BQ -->|discover partitions| P
    FIN -->|writer map| H1
    Client --> API
    API -->|GET, HEAD| PRO1
    API -->|write methods| PRW1
    PRW1 -->|local access_policy| PG1
```
