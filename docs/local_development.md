# Local Development

The `docker-compose.yaml` file runs the full pipeline on your machine. It uses MinIO instead of GCS. It uses a mock OIDC server for JWT tokens.

## Prerequisites

- Docker with Compose v2
- `gcloud` CLI authenticated for BigQuery access:
  ```bash
  gcloud auth application-default login
  ```

## Start the Stack

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

See [Using the API](using.md) for how to query a table once the stack is running.
