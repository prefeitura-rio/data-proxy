# Data Proxy

Data Proxy synchronises BigQuery tables to a PostgreSQL database (pg_duckdb) and exposes them through a PostgREST REST API, enforcing row-level security against a backend-managed access policy table, keyed by JWT identity claims.

BigQuery is the authoritative data store. PostgreSQL is a disposable, eventually consistent read cache.

## Documentation

- [Architecture](docs/architecture.md) — serving layer, sync pipeline, and deployment modes.
- [Sync](docs/sync.md) — `sync.json`, table strategies, partition batches, and indexes.
- [Using the API](docs/using.md) — PostgREST profiles, queries, freshness, and response headers.
- [Security](docs/security.md) — JWT roles, schema conditions, row conditions, and grant writes.
- [BigQuery Fallback](docs/fallback.md) — fallback reads and identity-aware caching.
- [KEDA Scaling](docs/keda.md) — worker streams, reclaim timeouts, and batch sizing.
- [Database Schema](docs/database.md) — PostgreSQL roles, tables, policies, and S3 access.
- [Environment Variables](docs/environment_variables.md) — application defaults and Helm environment conversion.
- [Helm Chart](docs/helm_chart.md) — chart installation, storage, upgrades, ingress, and fallback configuration.
- [Metrics](docs/metrics.md) — worker metrics, proxy logs, load limits, and Pushgateway scraping.
- [Backups](docs/backups.md) — encrypted `access_policy` backups and recovery.
- [Development](docs/development.md) — local Minikube setup and k6 commands.

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for the full text.
