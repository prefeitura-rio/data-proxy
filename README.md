# data-proxy

data-proxy synchronises BigQuery tables to a PostgreSQL database (pg\_duckdb) and exposes them through a PostgREST REST API with row-level security based on JWT claims.

BigQuery is the authoritative data store. PostgreSQL is a disposable, eventually consistent read cache.

## Service Objectives

| Indicator           |            Objective |
| ------------------- | -------------------: |
| Availability        |        99.9% monthly |
| Error rate          |         Less than 1% |
| API latency         |     p95 below 500 ms |
| Dataset freshness   |   Less than 26 hours |
| Full rebuild        |    Less than 4 hours |
| Failed-sync restart | Less than 30 minutes |

Availability applies to valid authenticated requests. Dataset freshness is measured from the last successful finalizer completion.

## How It Works

The pipeline has three components:

- **Producer** — runs as a Kubernetes CronJob. On each run it reads the sync configuration, compares every table's BigQuery modification signature against the last successful sync, and publishes tasks only for changed tables. It also writes the sync plan to Valkey. The pod exits when the plan is published.
- **Worker** — runs as a KEDA ScaledJob. KEDA creates one Job pod per pending message on the `dp:sync:tasks` stream. Each pod writes one task as a Parquet file to Google Cloud Storage and exits. Pods scale to zero between sync runs.
- **Finalizer** — runs as a KEDA ScaledJob. KEDA creates one Job pod per pending message on the `dp:sync:finalize` stream. The pod loads only the exact Parquet paths recorded in the sync plan, atomically publishes the changed tables to PostgreSQL, commits the new signatures, and signals PostgREST to reload its schema cache. At most one finalizer pod runs at a time.

When a BigQuery table has not changed since its last successful sync, the producer skips it. The producer compares a modification signature that combines the BigQuery modification time with the table's synchronization configuration. A configuration change therefore also forces a resync.

Signatures are committed only after the finalizer publishes successfully. Losing Valkey state causes a full resync, which is safe because BigQuery remains authoritative.

PostgREST serves the data through a REST API. The `pre_request` function reads the JWT claim and sets a PostgreSQL session variable. Row-level security policies use this variable to filter rows by organisational unit.

## Query a Table

Fetch a token from your identity provider, then query with the schema selected via the `Accept-Profile` header.

```bash
TOKEN="$(
  curl --fail --silent --show-error \
    --request POST \
    --header "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "grant_type=client_credentials" \
    --data-urlencode "client_id=${CLIENT_ID}" \
    --data-urlencode "client_secret=${CLIENT_SECRET}" \
    "${TOKEN_URL}" |
    jq --exit-status --raw-output '.access_token'
)"

curl --fail --silent --show-error \
  --header "Authorization: Bearer ${TOKEN}" \
  --header "Accept-Profile: ${SCHEMA}" \
  "${BASE_URL}/${TABLE}?select=col1,col2&limit=10"
```

## Architecture

### Standalone

Use standalone mode for development and single-region deployments.

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
    DB --> PGRST[PostgREST]
    PGRST -->|REST + JWT| Client([API Client])
```

### High Availability

Use HA mode for production. Patroni manages a three-node PostgreSQL cluster. PgBouncer separates write traffic (to the leader) from read traffic (to the replicas). The Kubernetes API serves as the distributed configuration store (DCS).

```mermaid
flowchart TD
    BQ[(BigQuery)]
    R[(Valkey\nStreams)]
    GCS[(GCS\nParquet)]
    K8S[(K8s API\nDCS)]

    subgraph pipeline[Sync pipeline]
        P[Producer\nCronJob] --> R --> W[Worker\nScaledJob]
        W --> GCS --> FIN[Finalizer\nScaledJob]
    end

    subgraph patroni[Patroni cluster]
        direction LR
        PGL[(Leader)] -->|WAL| PGR1[(Replica 1)]
        PGL -->|WAL| PGR2[(Replica 2)]
    end

    BQ -->|discover partitions| P
    FIN -->|write| PBrw[PgBouncer\nrw]
    PBrw --> PGL
    PGL & PGR1 & PGR2 <-->|leader election| K8S
    PGRST[PostgREST] -->|read| PBro[PgBouncer\nro]
    PBro --> PGR1 & PGR2
    PGRST -->|REST + JWT| Client([API Client])
```

## Sync Configuration

The sync configuration is a JSON file. Set `SYNC_CONFIG_PATH` to its location.

```json
{
  "tables": [
    {
      "bq_table": "project.dataset.table",
      "strategy": "dump",
      "pg_schema": "my_schema",
      "rls": { "column": "unit_id" }
    },
    {
      "bq_table": "project.dataset.events",
      "strategy": "window",
      "pg_schema": "my_schema",
      "partition": {
        "column": "data_particao",
        "n": 7
      },
      "indexes": [{ "name": "idx_events_unit", "columns": ["unit_id"] }],
      "rls": { "column": "unit_id" }
    }
  ]
}
```

| Field              | Required    | Description                                                                        |
| ------------------ | ----------- | ---------------------------------------------------------------------------------- |
| `bq_table`         | yes         | Full BigQuery table reference (`project.dataset.table`).                           |
| `strategy`         | yes         | `dump` replaces the full table. `window` replaces the last _n_ partitions.         |
| `pg_schema`        | no          | Target PostgreSQL schema. The default is the BigQuery dataset name.                |
| `rls.column`       | no          | Column used for row-level security. Omit this field to disable RLS on the table.   |
| `indexes`          | no          | Array of `{ name, columns }` objects. Creates one index per entry after each sync. |
| `partition.column` | window only | BigQuery partition column name.                                                    |
| `partition.n`      | window only | Number of most-recent partitions to sync.                                          |

## Environment Variables

All pipeline components (producer, worker, finalizer) read these variables.

| Variable                         | Default                                      | Description                                                                                                               |
| -------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `PG_DSN`                         | `postgresql://test:test@localhost:5432/test` | PostgreSQL connection string. In HA mode, this points directly to the leader to preserve DuckDB libpq session state.      |
| `REDIS_URL`                      | `redis://localhost:6379/0`                   | Valkey (Redis-compatible) connection URL for the task queue.                                                              |
| `GCS_BUCKET`                     | `test-bucket`                                | Name of the GCS bucket that stores Parquet files.                                                                         |
| `GCS_ENDPOINT`                   | `localhost:9000`                             | GCS endpoint host and port. Leave empty to use real GCS. Set to `host:port` for MinIO.                                    |
| `GCS_USE_SSL`                    | `false`                                      | Set to `true` when you connect to real GCS.                                                                               |
| `GCS_KEY_ID`                     | —                                            | HMAC key ID for GCS access.                                                                                               |
| `GCS_SECRET_KEY`                 | —                                            | HMAC secret key for GCS access.                                                                                           |
| `SYNC_CONFIG_PATH`               | `config/sync.json`                           | Path to the sync configuration file.                                                                                      |
| `GOOGLE_APPLICATION_CREDENTIALS` | —                                            | Path to a GCP service account JSON file for BigQuery access. This variable is not required on GKE with Workload Identity. |
| `WORKER_MAX_RECORDS`             | `1`                                          | Maximum number of stream messages a worker pod processes per run.                                                         |
| `AUTH_ANON_ROLE`                 | `web_anon`                                   | PostgreSQL role PostgREST uses for unauthenticated requests.                                                              |
| `AUTH_USER_ROLE`                 | `web_user`                                   | PostgreSQL role PostgREST switches to for authenticated requests.                                                         |
| `AUTH_AUTHENTICATOR_ROLE`        | `authenticator`                              | PostgreSQL login role PostgREST connects as.                                                                              |

## Helm Chart

### Prerequisites

The following components must be installed in the cluster before you deploy the chart:

- [KEDA](https://keda.sh/docs/latest/deploy/) — required for the `ScaledJob` and `TriggerAuthentication` resources that drive the worker and finalizer.

### Install

The chart publishes to `oci://ghcr.io/prefeitura-rio/charts`.

```bash
helm install data-proxy \
  oci://ghcr.io/prefeitura-rio/charts/data-proxy \
  --version <chart-version> \
  --values my-values.yaml
```

See [`helm/values.yaml`](helm/values.yaml) for the full list of configuration options and their descriptions.

### Enable HA

Add the following to your values file and set `ha.patroni.image` to an image built from `Dockerfile.patroni`.

```yaml
ha:
  enabled: true
  patroni:
    image: ghcr.io/prefeitura-rio/data-proxy-patroni:latest
    replicationPassword: "<strong-password>"
```

### Versioning

The Helm pipeline increments the minor version on each release. Do not change `helm/Chart.yaml` manually. A major version change indicates a breaking change.

## Local Development

The `docker-compose.yaml` file emulates the full pipeline locally. It replaces GCS with MinIO and uses a mock OIDC server for JWT tokens.

### Prerequisites

- Docker with Compose v2
- `gcloud` CLI authenticated for BigQuery access:
  ```bash
  gcloud auth application-default login
  ```

### Start the Stack

```bash
docker compose up --build
```

| Service   | Port        | Description                                  |
| --------- | ----------- | -------------------------------------------- |
| pgduckdb  | 5544        | PostgreSQL with the pg\_duckdb extension.    |
| PostgREST | 3111        | REST API.                                    |
| Redis     | 6379        | Sync task queue.                             |
| MinIO     | 9000 / 9001 | S3-compatible object storage (replaces GCS). |
| OIDC mock | 8081        | Issues JWT tokens for local testing.         |

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for the full text.
