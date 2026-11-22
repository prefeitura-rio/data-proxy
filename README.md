# Data Proxy

Data Proxy synchronises the tables of an external source to PostgreSQL with the pg_duckdb extension. It exposes them through a PostgREST REST API. Row-level security uses a backend-managed access policy table, keyed by JWT identity claims.

The source is the authoritative data store. BigQuery is the only source adapter today. PostgreSQL is a disposable, eventually consistent read cache.

## Documentation

The documentation is a book of linked Markdown files in `docs/`. Read the [latest publication](https://prefeitura-rio.github.io/data-proxy/latest/) or download its [LLM bundle](https://prefeitura-rio.github.io/data-proxy/latest/llms.txt). Each chart release also publishes a version folder, such as `v2.21.0/`. The latest publication can be an older release when you publish that release again.

- [Architecture](docs/architecture/index.md) — components, DuckLake pieces and the serving, sync and console layers.
- [Sync](docs/sync.md) — `sync.json`, table strategies, partition batches and DuckLake sort keys.
- [Using the API](docs/using.md) — PostgREST profiles, queries and response headers.
- [Proxy](docs/proxy/index.md) — routing headers and identity-aware caching.
- [Database Schema](docs/database.md) — PostgreSQL roles, tables, policies and S3 access.
- [Notebooks](docs/notebooks.md) — the notebook site of the query tab.
- [SQL Templates](docs/sql.md) — how the SQL templates are named, written and rendered.
- [Security](docs/security/index.md) — JWT claims, permissions, authorization, tokens and the identity provider.
- [Console](docs/console/index.md) — the admin web app and its database views.
- [Development](docs/dev/index.md) — local setup, devenv, checks, tests and CI.
- [Operations](docs/operations/index.md) — Helm chart, topology, settings, scaling, metrics and backups.

### Browse the book

The pages link to each other with relative Markdown links. Run `devenv --profile docs up docs` to read them in a browser, in the dark theme or the light ones. The [documentation rules](docs/style.md#documentation) lists the rules for a page.

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for the full text.
