# Architecture

## Serving layer

BigQuery is the source of truth. PostgreSQL is the normal read store. PostgREST exposes synced tables. When fallback is enabled, nginx reads local PostgREST first and then reads the BigQuery-backed `_bq` view only for an empty local `GET` response.

Parquet files live in an S3-compatible object store. The chart default is SeaweedFS. PostgreSQL uses the pg_duckdb extension to read the files during publication. Clients never call BigQuery directly.

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

#### Sync

```mermaid
sequenceDiagram
    participant BQ as BigQuery
    participant P as Producer
    participant R as Valkey
    participant W as Dumper
    participant S3 as S3/SeaweedFS
    participant S as Seeder
    participant PUB as Publisher
    participant CW as CNPG writer
    participant CR as CNPG replica
    participant RO as PostgREST-ro
    participant RW as PostgREST-rw

    Note over P: CronJob trigger
    P->>BQ: discover changed tables and partitions
    P->>R: publish extract tasks
    Note over W: ScaledObject scales on stream length
    W->>R: consume extract task
    W->>BQ: extract rows
    W->>S3: write Parquet
    W->>R: publish seed task
    Note over S: ScaledJob scales on stream length
    S->>R: consume seed task
    S->>R: publish publish task
    Note over PUB: ScaledJob scales on stream length
    PUB->>S3: read Parquet
    PUB->>CW: load and commit local tables
    CW-->>CR: stream WAL when a replica exists
    PUB->>CR: wait for publication WAL replay
    PUB->>RO: refresh deployment and wait for HTTP readiness
    PUB->>RW: refresh deployment and wait for HTTP readiness
    PUB->>S3: cleanup consumed files
    PUB->>R: commit publication state and flush cache
```

#### Request

```mermaid
sequenceDiagram
    participant C as Client
    participant N as nginx
    participant R as Valkey
    participant RO as PostgREST-ro
    participant RP as CNPG Pooler RO
    participant RW as PostgREST-rw
    participant CW as CNPG writer
    participant BQ as BigQuery

    C->>N: GET or HEAD /table (JWT)
    N->>R: cache lookup
    alt cache hit
        R-->>N: cached rows
        N-->>C: 200 (from cache)
    else cache miss
        R-->>N: miss
        N->>RO: read request
        RO->>RP: SELECT through read Pooler
        RP->>CW: read current writer or replica
        alt local rows present
            RP-->>RO: rows
            RO-->>N: 200 (local)
        else local table empty
            RO->>RP: SELECT ... FROM table_bq
            RP->>BQ: _bq view query
            BQ-->>RP: fallback rows
            RP-->>RO: fallback rows
            RO-->>N: 200 (fallback)
        end
        N->>R: cache store
        N-->>C: 200
    end
    C->>N: POST, PUT, PATCH, or DELETE /table (JWT)
    N->>RW: mutation request
    RW->>CW: write to current writer
    CW-->>RW: response
    RW-->>N: response
    N-->>C: response
```

### High Availability

CNPG manages PostgreSQL instances, replication, failover, and lifecycle. In shared mode, one CNPG Cluster serves all configured schemas. In per-schema HA mode, each configured schema has its own CNPG Cluster, read Pooler, nginx proxy, and PostgREST-ro/rw pair.

Nginx routes `GET` and `HEAD` requests to PostgREST-ro through the CNPG read Pooler. Mutations route to PostgREST-rw, which connects directly to the current CNPG writer. The read Pooler is transaction-pooled and is not used by sync workers or PostgREST writes.

The Publisher commits database state, waits for standby WAL replay, refreshes both PostgREST deployments, and waits for their HTTP readiness probes before it completes publication and flushes the response cache. `/access_policy` is never response-cached.

#### Sync

```mermaid
sequenceDiagram
    participant BQ as BigQuery
    participant P as Producer
    participant R as Valkey
    participant W as Dumper
    participant S3 as S3/SeaweedFS
    participant S as Seeder
    participant PUB as Publisher
    participant CW as CNPG writer
    participant CR as CNPG replica
    participant RO as PostgREST-ro
    participant RW as PostgREST-rw

    Note over P: CronJob trigger
    P->>BQ: discover changed tables and partitions
    P->>R: publish extract tasks
    Note over W: ScaledObject scales on stream length
    W->>R: consume extract task
    W->>BQ: extract rows
    W->>S3: write Parquet
    W->>R: publish seed task
    Note over S: ScaledJob scales on stream length
    S->>R: consume seed task
    S->>R: publish publish task
    Note over PUB: ScaledJob scales on stream length
    PUB->>S3: read Parquet
    PUB->>CW: load and commit schema tables
    CW-->>CR: stream WAL
    PUB->>CR: wait for publication WAL replay
    PUB->>RO: refresh deployment and wait for HTTP readiness
    PUB->>RW: refresh deployment and wait for HTTP readiness
    PUB->>S3: cleanup consumed files
    PUB->>R: commit publication state and flush cache
```

#### Request

```mermaid
sequenceDiagram
    participant C as Client
    participant N as nginx
    participant R as Valkey
    participant RO as PostgREST-ro
    participant RP as CNPG Pooler RO
    participant RW as PostgREST-rw
    participant CW as CNPG writer

    C->>N: GET or HEAD /table (JWT)
    N->>R: cache lookup
    alt cache hit
        R-->>N: cached rows
        N-->>C: 200 (from cache)
    else cache miss
        R-->>N: miss
        N->>RO: route read
        RO->>RP: SELECT through read Pooler
        RP-->>RO: rows or fallback result
        RO-->>N: response
        N->>R: cache non-empty response
        N-->>C: response
    end
    C->>N: POST, PUT, PATCH, or DELETE /table (JWT)
    N->>RW: route mutation
    RW->>CW: write to current writer
    CW-->>RW: response
    RW-->>N: response
    N-->>C: response
```

Publication is atomic inside one schema database. It is not atomic across independent schema databases. If one schema publishes and a later schema fails, the Publisher does not commit synchronization state. A retry can publish an already-published schema again. Publication operations must remain idempotent.

### Mode migration

A shared-to-per-schema or per-schema-to-shared change is a normal Helm upgrade. During the post-upgrade migration hook, Helm retains the source topology, initializes the target, copies each configured schema with idempotent `pg_dump`/`pg_restore`, and records the result in `data-proxy-mode-state`. A later reconciliation prunes the retained source resources.

A failed migration keeps the source available for retry. Do not manually delete CNPG or application resources during a transition.

---

[Home](../README.md) · [Next →](sync.md)
