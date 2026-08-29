# Data Proxy

Data Proxy synchronises BigQuery tables to a PostgreSQL database (pg\_duckdb) and exposes them through a PostgREST REST API, enforcing row-level security against a backend-managed access policy table, keyed by JWT identity claims.

BigQuery is the authoritative data store. PostgreSQL is a disposable, eventually consistent read cache.

## Documentation

- [Architecture](docs/architecture.md) — service objectives, pipeline components, standalone and HA diagrams.
- [Sync Configuration](docs/sync.md) — the `sync.json` file, table fields, and strategy selection.
- [Security](docs/security.md) — creating users and row-level security through `rls.access_policy`.
- [Backups](docs/backups.md) — encrypted backups of `rls.access_policy`, and how to restore one.
- [Using the API](docs/using.md) — querying tables through PostgREST: filtering, ordering, pagination, counting.
- [Environment Variables](docs/environment_variables.md) — configuration read by the producer, worker, and finalizer.
- [Helm Chart](docs/helm_chart.md) — installing and configuring the chart, including HA mode.
- [Local Development](docs/development.md) — running the full pipeline on Kubernetes with Minikube and Helm.

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for the full text.
