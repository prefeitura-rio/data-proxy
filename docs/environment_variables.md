# Environment Variables

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
| `AUTH_ANON_ROLE`                 | `anon`                                       | PostgreSQL role PostgREST uses for unauthenticated requests.                                                              |
| `AUTH_USER_ROLE`                 | `user`                                       | PostgreSQL role PostgREST switches to for authenticated requests.                                                         |
| `AUTH_AUTHENTICATOR_ROLE`        | `authenticator`                              | PostgreSQL login role PostgREST connects as.                                                                              |
