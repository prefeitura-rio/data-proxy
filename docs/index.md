# Data Proxy

Data Proxy syncs the tables of an external source to PostgreSQL and serves them through PostgREST. This wiki contains the design, the rules and the operation of the system. [Glossary](glossary.md) defines the terms that the pages use.

## Start here

Read these four pages in this order:

1. [Architecture](architecture/index.md) answers: what are the parts, and how do they depend on each other?
2. [Sync](sync.md) answers: what does Data Proxy sync, and how do you configure it?
3. [Using the API](using.md) answers: how do you query the data?
4. [Security](security/index.md) answers: who can read what?

## All pages

- [Architecture](architecture/index.md) — Components, authentication, authorization and DuckLake pieces.
  - [Serving layer](architecture/serving.md) — Answers API reads with the cache, PgDog and DuckLake.
  - [Sync layer](architecture/sync.md) — Copies source tables into DuckLake.
  - [Console layer](architecture/console.md) — The web app, its sign-in and the run control.
- [Sync](sync.md) — The `sync.json` settings, table strategies, partitions and DuckLake sort keys.
- [Using the API](using.md) — PostgREST profiles, queries and response headers.
- [Proxy](proxy.md) — Routing headers and identity-aware caching.
- [Database Schema](database.md) — PostgreSQL roles, tables, access policies and S3 access.
- [Data model](model.md) — Diagrams of the tables, columns and keys.
- [Glossary](glossary.md) — The terms and the tools that the pages use.
- [Notebooks](notebooks.md) — The notebook site of the query tab.
- [SQL Templates](sql.md) — How the SQL templates are named, written and rendered.
- [Security](security/index.md) — JSON Web Token (JWT) claims, permissions, authorization, tokens and the identity provider.
- [Console](console/index.md) — The admin web app and its database views.
- [Local Development](development/index.md) — Local setup, tests, Nix, migrations and k6 commands.
- [Operations](operations/index.md) — Helm chart, topology, settings, scaling, metrics and backups.

<!-- nav:start -->
---

[Next: Architecture](architecture/index.md)
<!-- nav:end -->
