# data-proxy

[![CI](https://github.com/iplanrio/data-proxy/actions/workflows/ci.yaml/badge.svg)](https://github.com/iplanrio/data-proxy/actions/workflows/ci.yaml)
[![Helm chart](https://github.com/iplanrio/data-proxy/actions/workflows/helm.yaml/badge.svg)](https://github.com/iplanrio/data-proxy/actions/workflows/helm.yaml)

data-proxy synchronises BigQuery tables to a PostgreSQL database (pg\_duckdb) and exposes them through a PostgREST REST API with row-level security based on JWT claims.

## How It Works

The pipeline has three services:

- **Producer** — reads the sync configuration, queries BigQuery for partition values, and sends one task per table to a Redis stream.
- **Worker** — consumes tasks from the stream and writes each table as a Parquet file to Google Cloud Storage.
- **Finalizer** — reads the Parquet files from GCS with DuckDB, writes the data to PostgreSQL, and signals PostgREST to reload its schema cache.

PostgREST serves the data through a REST API. The `pre_request` function reads the JWT claim and sets a PostgreSQL session variable. Row-level security policies use this variable to filter rows by organisational unit.

## Architecture

### Standalone

Use standalone mode for development and single-region deployments.

```mermaid
flowchart TD
    BQ[(BigQuery)]
    R[(Redis\nStreams)]
    GCS[(GCS\nParquet)]
    DB[(pgduckdb)]

    subgraph pipeline[Sync pipeline]
        P[Producer] --> R --> W[Worker]
        W --> GCS --> FIN[Finalizer]
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
    R[(Redis\nStreams)]
    GCS[(GCS\nParquet)]
    K8S[(K8s API\nDCS)]

    subgraph pipeline[Sync pipeline]
        P[Producer] --> R --> W[Worker]
        W --> GCS --> FIN[Finalizer]
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
      "rls": { "column": "unit_id" }
    }
  ]
}
```

| Field | Required | Description |
|---|---|---|
| `bq_table` | yes | Full BigQuery table reference (`project.dataset.table`). |
| `strategy` | yes | `dump` replaces the full table. `window` replaces the last *n* partitions. |
| `pg_schema` | no | Target PostgreSQL schema. The default is the BigQuery dataset name. |
| `rls.column` | no | Column used for row-level security. Omit this field to disable RLS on the table. |
| `partition.column` | window only | BigQuery partition column name. |
| `partition.n` | window only | Number of most-recent partitions to sync. |

## Environment Variables

All pipeline services (producer, worker, finalizer) read these variables.

| Variable | Default | Description |
|---|---|---|
| `PG_DSN` | `postgresql://test:test@localhost:5432/test` | PostgreSQL connection string. In HA mode, this points directly to the leader to preserve DuckDB libpq session state. |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL for the task queue. |
| `GCS_BUCKET` | `test-bucket` | Name of the GCS bucket that stores Parquet files. |
| `GCS_ENDPOINT` | `localhost:9000` | GCS endpoint host and port. Leave empty to use real GCS. Set to `host:port` for MinIO. |
| `GCS_USE_SSL` | `false` | Set to `true` when you connect to real GCS. |
| `GCS_KEY_ID` | — | HMAC key ID for GCS access. |
| `GCS_SECRET_KEY` | — | HMAC secret key for GCS access. |
| `SYNC_CONFIG_PATH` | `config/sync.json` | Path to the sync configuration file. |
| `GOOGLE_APPLICATION_CREDENTIALS` | — | Path to a GCP service account JSON file for BigQuery access. This variable is not required on GKE with Workload Identity. |

## Helm Chart

The chart publishes to `oci://ghcr.io/iplanrio/charts`.

```bash
helm install data-proxy \
  oci://ghcr.io/iplanrio/charts/data-proxy \
  --version 1.0.0 \
  --values my-values.yaml
```

See [`helm/values.yaml`](helm/values.yaml) for the full list of configuration options and their descriptions.

### Enable HA

Add the following to your values file and set `ha.patroni.image` to an image built from `Dockerfile.patroni`.

```yaml
ha:
  enabled: true
  patroni:
    image: ghcr.io/iplanrio/data-proxy-patroni:latest
    replicationPassword: "<strong-password>"
```

### Versioning

The chart uses two-part versioning (`MAJOR.MINOR.0`). The pipeline increments the minor version on each release. A major version change indicates a breaking change and requires a manual update to `helm/Chart.yaml`.

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

| Service | Port | Description |
|---|---|---|
| pgduckdb | 5544 | PostgreSQL with the pg\_duckdb extension. |
| PostgREST | 3111 | REST API. |
| Redis | 6379 | Sync task queue. |
| MinIO | 9000 / 9001 | S3-compatible object storage (replaces GCS). |
| OIDC mock | 8081 | Issues JWT tokens for local testing. |

### Get a Token

```bash
curl -s -X POST http://localhost:8081/default/token \
  -d "grant_type=client_credentials" \
  -d "client_id=dev-client" | jq -r .access_token
```

### Query the API

```bash
curl -H "Authorization: Bearer <token>" \
  http://localhost:3111/<table>
```

Replace `<table>` with the name of a synced table.

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for the full text.
