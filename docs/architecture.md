# Architecture

## Serving layer

BigQuery is the source of truth. PostgreSQL is the normal read store. PostgREST exposes synced tables. When fallback is enabled, nginx reads local PostgREST first and then reads the BigQuery-backed `_bq` view only for an empty local `GET` response.

Parquet files live in an S3-compatible object store. The chart default is SeaweedFS. pg_duckdb reads the files during publication. Clients never call BigQuery directly.

Webdis caches non-empty JSON responses with identity-aware keys. PostgREST validates JWTs and applies the same row-level security to local tables and `_bq` views. See [Fallback](fallback.md) and [Security](security.md).

## Sync pipeline

| Component | Work                                              | Result                                                              |
| --------- | ------------------------------------------------- | ------------------------------------------------------------------- |
| Producer  | Detect changed tables and partitions.             | Stores plans and publishes tasks to Valkey.                         |
| Dumper    | Extract one full table or one partition batch.    | Writes one Parquet file and records the result.                     |
| Seeder    | Initialize configured schemas and policy objects. | Publishes one schema task per plan.                                 |
| Publisher | Load, prepare, and publish one schema.            | Commits table state and refreshes PostgREST after the final schema. |

The Producer includes configuration in a table signature. A configuration change therefore causes a resync.

A partition batch contains changed partitions up to the configured size and count limits. One batch produces one Parquet file. See [Sync](sync.md).

Full tables and partitioned full rebuilds publish atomically. An incremental update keeps old data for a failed existing partition and omits a failed new partition. The next run schedules failed partitions again.

## Modes

### Standalone

Use standalone mode for development and single-region deployments.

```mermaid
sequenceDiagram
    participant BQ as BigQuery
    participant P as Producer
    participant R as Valkey
    participant W as Dumper
    participant S3 as SeaweedFS
    participant S as Seeder
    participant FIN as Publisher
    participant DB as pg_duckdb
    participant PGRST as PostgREST

    Note over P: CronJob trigger
    P->>BQ: discover changed tables & partitions
    P->>R: publish extract tasks

    Note over W: ScaledObject scales on stream length
    W->>R: consume extract task
    W->>BQ: extract rows
    W->>S3: write Parquet
    W->>R: publish seed task

    Note over S: ScaledJob scales on stream length
    S->>R: consume seed task
    S->>R: publish publish task

    Note over FIN: ScaledJob scales on stream length
    FIN->>R: consume publish task
    FIN->>S3: read Parquet
    FIN->>DB: load into local table
    FIN->>S3: cleanup consumed files
    FIN->>PGRST: refresh schema
```

```mermaid
sequenceDiagram
    participant C as Client
    participant N as nginx
    participant R as Valkey
    participant P as PostgREST
    participant DB as pg_duckdb
    participant BQ as BigQuery

    C->>N: GET /table (JWT)
    N->>R: cache lookup
    alt cache hit
        R-->>N: cached rows
        N-->>C: 200 (from cache)
    else cache miss
        R-->>N: miss
        N->>P: GET /table (JWT)
        P->>DB: SELECT … FROM table
        alt local rows present
            DB-->>P: rows
            P-->>N: 200 (local)
        else local table empty
            P->>DB: SELECT … FROM table_bq
            DB->>BQ: _bq view query
            BQ-->>DB: fallback rows
            DB-->>P: rows
            P-->>N: 200 (fallback)
        end
        N->>R: cache store
        N-->>C: 200
    end
```

### High Availability

HA mode creates one independent Patroni and HAProxy stack for each configured application schema. Patroni uses the Kubernetes API as its distributed configuration store (DCS). HAProxy port `5000` sends PostgreSQL connections to the current primary. HAProxy port `5001` sends connections to replicas.

Publication is atomic inside one schema database. It is not atomic across independent schema databases. If one schema publishes and a later schema fails, the Publisher does not commit synchronization state. A retry can publish an already-published schema again. Publication operations must remain idempotent.

```mermaid
flowchart TD
    BQ[(BigQuery)]
    R[(Valkey\nStreams)]
    S3[(SeaweedFS\nParquet)]
    API[Istio\nVirtualService]
    Client([API Client])

    subgraph pipeline[Shared sync pipeline]
        P[Producer\nCronJob] --> R --> W[Dumper\nScaledObject]
        W --> S3 --> S[Seeder\nScaledJob] --> FIN[Publisher\nScaledJob]
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

---

[Home](../README.md) · [Next →](sync.md)
