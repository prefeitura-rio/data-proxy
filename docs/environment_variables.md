# Environment Variables

Application defaults below apply outside Helm. Helm can override them.

| Variable                          | Application default                                               | Meaning                                                     |
| --------------------------------- | ----------------------------------------------------------------- | ----------------------------------------------------------- |
| `PG_DSN`                          | `postgresql://test:test@localhost:5432/test`                      | PostgreSQL DSN.                                             |
| `REDIS_URL`                       | `redis://localhost:6379/0`                                        | Valkey stream and state URL.                                |
| `S3_BUCKET`                       | `test-bucket`                                                     | Parquet bucket.                                             |
| `S3_ENDPOINT`                     | `localhost:8333`                                                  | S3 endpoint host and port.                                  |
| `S3_USE_SSL`                      | `false`                                                           | Use TLS for S3.                                             |
| `S3_ACCESS_KEY`                   | `seaweedfs`                                                       | S3 access key.                                              |
| `S3_SECRET_KEY`                   | `seaweedfs-local`                                                 | S3 secret key.                                              |
| `SYNC_CONFIG_PATH`                | `config/sync.json`                                                | Sync configuration path.                                    |
| `GOOGLE_APPLICATION_CREDENTIALS`  | —                                                                 | BigQuery service-account file. Omit with Workload Identity. |
| `PRODUCER_POLL_INTERVAL_SECONDS`  | `60`                                                              | Active-run poll interval.                                   |
| `DUMPER_MAX_RETRIES`              | `3`                                                               | Dumper retry limit.                                         |
| `DUMPER_VISIBILITY_TIMEOUT_MS`    | `900000`                                                          | Application dump reclaim timeout.                           |
| `SEEDER_VISIBILITY_TIMEOUT_MS`    | `900000`                                                          | Seed reclaim timeout.                                       |
| `PUBLISHER_VISIBILITY_TIMEOUT_MS` | `7200000`                                                         | Publish reclaim timeout.                                    |
| `DUMPER_BATCH_BYTES`              | `629145600`                                                       | Uncompressed batch target in bytes.                         |
| `DUMPER_BATCH_MAX_PARTITIONS`     | `256`                                                             | Maximum partitions in one dump task.                        |
| `DUMPER_SCRATCH_DIR`              | System temporary directory                                        | Local Parquet merge directory.                              |
| `FALLBACK_ENABLED`                | `false`                                                           | Enable fallback views and proxy behavior.                   |
| `FALLBACK_CACHE_REDIS_DB`         | `1`                                                               | Valkey database for fallback cache.                         |
| `AUTH_ANON_ROLE`                  | `anon`                                                            | Unauthenticated PostgreSQL role.                            |
| `AUTH_USER_ROLE`                  | `user`                                                            | Authenticated PostgreSQL role.                              |
| `AUTH_AUTHENTICATOR_ROLE`         | `authenticator`                                                   | PostgREST login role.                                       |
| `SCHEMA_WRITERS_FILE`             | `config/schema-writers/writers.json`                              | Schema writer DSN mapping.                                  |
| `PUSHGATEWAY_URL`                 | `http://data-proxy-pushgateway.data-proxy.svc.cluster.local:9091` | Pushgateway URL.                                            |

## Helm conversion

Helm accepts the batch target as MiB:

```yaml
dumper:
  batchMegaBytes: 600
```

The chart converts this value to `DUMPER_BATCH_BYTES` by multiplying it by `1048576`. The Helm Dumper timeout default is `3600000` ms, which overrides the application default.

## Fallback

Configure nginx fallback behavior with Helm values under `fallback`. See [Fallback](fallback.md) for request and cache behavior.

---

[← Previous](database.md) · [Home](../README.md) · [Next →](helm_chart.md)
