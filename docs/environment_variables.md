# Environment Variables

All pipeline components (producer, Dumper, Publisher) read these variables.

| Variable                         | Default                                      | Description                                                                                                               |
| -------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `PG_DSN`                         | `postgresql://test:test@localhost:5432/test` | PostgreSQL connection string. In HA mode, use the leader's address. This keeps the DuckDB libpq session state correct.    |
| `REDIS_URL`                      | `redis://localhost:6379/0`                   | Valkey (Redis-compatible) connection URL for the task queue.                                                              |
| `GCS_BUCKET`                     | `test-bucket`                                | Name of the GCS bucket that stores Parquet files.                                                                         |
| `GCS_ENDPOINT`                   | `localhost:9000`                             | GCS endpoint host and port. Leave empty to use real GCS. Set to `host:port` for MinIO.                                    |
| `GCS_USE_SSL`                    | `false`                                      | Set to `true` when you connect to real GCS.                                                                               |
| `GCS_KEY_ID`                     | `minioadmin`                                 | HMAC key ID for GCS access.                                                                                               |
| `GCS_SECRET_KEY`                 | `minioadmin`                                 | HMAC secret key for GCS access.                                                                                           |
| `SYNC_CONFIG_PATH`               | `config/sync.json`                           | Path to the sync configuration file.                                                                                      |
| `GOOGLE_APPLICATION_CREDENTIALS` | —                                            | Path to a GCP service account JSON file for BigQuery access. Skip this variable on GKE with Workload Identity.            |
| `DUMPER_VISIBILITY_TIMEOUT_MS`    | `900000`                                     | Time a dump task must stay pending before a new Dumper can reclaim it. Set a value longer than normal dump duration. |
| `SEEDER_VISIBILITY_TIMEOUT_MS`    | `900000`                                     | Time a seed task must stay pending before a new Seeder can reclaim it. |
| `PUBLISHER_VISIBILITY_TIMEOUT_MS` | `900000`                                     | Time a publish task must stay pending before a new Publisher can reclaim it. Set a value longer than normal publication duration. |
| `AUTH_ANON_ROLE`                 | `anon`                                       | PostgreSQL role PostgREST uses for unauthenticated requests.                                                              |
| `AUTH_USER_ROLE`                 | `user`                                       | PostgreSQL role PostgREST switches to for authenticated requests.                                                         |
| `AUTH_AUTHENTICATOR_ROLE`        | `authenticator`                              | PostgreSQL login role PostgREST connects as.                                                                              |
| `SCHEMA_WRITERS_FILE`            | `config/schema-writers/writers.json`         | Path to the schema-to-writer DSN mapping file.                                                                            |
