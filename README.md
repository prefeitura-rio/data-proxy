# Data Proxy

Data Proxy synchronises the tables of an external source to PostgreSQL with the pg_duckdb extension and exposes them through a PostgREST REST API, enforcing row-level security against a backend-managed access policy table, keyed by JWT identity claims.

The source is the authoritative data store. BigQuery is the only source adapter today. PostgreSQL is a disposable, eventually consistent read cache.

## Documentation

The documentation is a wiki of linked Markdown files in `docs/`. Start at [docs/index.md](docs/index.md).

- [Architecture](docs/architecture/index.md) — components, DuckLake pieces, and the serving, sync and console layers.
- [Sync](docs/sync.md) — `sync.json`, table strategies, partition batches, and DuckLake sort keys.
- [Using the API](docs/using.md) — PostgREST profiles, queries, and response headers.
- [Proxy](docs/proxy/index.md) — routing headers and identity-aware caching.
- [Database Schema](docs/database.md) — PostgreSQL roles, tables, policies, and S3 access.
- [Notebooks](docs/notebooks.md) — the notebook site of the query tab.
- [SQL Templates](docs/sql.md) — how the SQL templates are named, written, and rendered.
- [Security](docs/security/index.md) — JWT claims, permissions, authorization, tokens, and the identity provider.
- [Console](docs/console/index.md) — the admin web app and its database views.
- [Development](docs/development/index.md) — local setup, tests, Nix, migrations, and k6 commands.
- [Operations](docs/operations/index.md) — Helm chart, topology, settings, scaling, metrics, and backups.

### Browse the wiki

The pages link to each other with relative Markdown links. Open the repository in an editor that has the [Marksman](https://github.com/artempyanykh/marksman) language server, which the devenv shell provides. Then use go to definition on a link, find references on a heading, and completion inside a link target. The [style guide](STYLEGUIDE.md#documentation) lists the rules for a page.

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for the full text.
