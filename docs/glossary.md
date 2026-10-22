# Glossary

This page defines the terms that the other pages use. Each term has one meaning on every page, except the word schema, which has three. The Tools table explains the tools that the pages name.

## Terms

| Term | Meaning |
| --- | --- |
| Access policy | The table `<schema>.access_policy`. Each row names a subject, a unit type and a unit id, and it says which rows that subject may read. |
| Adapter | The code that reads one kind of source. BigQuery is the only adapter today. |
| Catalog | The DuckLake metadata of one schema. It lives in the `ducklake` database, in a metadata schema with the name of the schema. |
| Claim | A field in the JSON Web Token. The `role` claim names the role, and the `schemas` claim lists the schemas that the user may use. |
| Fallback | A table setting. With `fallback: true`, the table function reads the partitions that DuckLake doesn't store from the source. |
| Job | A Kubernetes Job. The chart runs jobs such as `provision`, and the sync worker creates one from a suspended CronJob on demand. |
| Permission | One right in `rbac.toml`, such as `read_sync`. A role has permissions. |
| Provision | The Kubernetes Job that creates the PostgreSQL schema, the access policy and the grants for each configured schema. |
| Queue | A DBOS queue that stores workflows until a worker takes them, such as `sync`, `dump`, `publish` and `control`. |
| Resume | Put a cancelled sync run back in its queue. The console action is `resume_run`. |
| Role | One of `dp_api`, `dp_policy`, `dp_console` and `dp_admin`. The database has a role with the same name. |
| Row policy | A PostgreSQL row-level security (RLS) policy. It limits which rows of a table a role may read or write. It isn't a row of the access policy. |
| Sync run | One execution of the `run_sync` workflow. A sync run has tasks. |
| Scope | The schemas and tables that a sync run covers. The table `data_proxy.scope` records it. |
| Snapshot | One version of a DuckLake catalog. Each publication commits one snapshot. |
| Source | The external system that contains the original tables. |
| Step | One recorded operation inside a workflow. |
| Task | A workflow that a sync run starts: a `dump_task` or a `publish_schema`. |
| Workflow | A durable DBOS function that survives a restart, such as `run_sync`. |

## Tools

| Tool | Role in Data Proxy |
| --- | --- |
| PostgreSQL | The database. It stores the configuration, the access policy, the DuckLake catalogs and the DBOS state. |
| PostgREST | An HTTP server that turns PostgreSQL views and functions into a REST API. |
| PgDog | A connection pooler. It sends each read to a PostgreSQL standby and each write to the primary. |
| DuckLake | A table format. It keeps table metadata in PostgreSQL and table data in Parquet files. |
| DuckDB | A SQL engine. The `pg_duckdb` extension runs it inside PostgreSQL to read Parquet files. |
| Parquet | A file format that stores table data in columns. |
| SeaweedFS | The S3-compatible object storage that contains the Parquet files. |
| Redis | The cache of the proxy. The tests use Valkey, a compatible server. |
| OpenResty | nginx with Lua. The proxy runs on it. |
| Istio gateway | The entry point of the cluster. It checks the JWT before it forwards a request. |
| Keycloak | The identity provider of the local and test clusters. |
| goose | The tool that applies the console database migrations. |
| k6 | The tool for load tests and end-to-end checks of a cluster. |
| Hurl | The tool for the HTTP tests of the proxy. |
| devenv | The tool for the local environment, on Nix. It starts services and runs tasks. |
| prek | The tool that runs the Git hooks. |

## Schema

The word schema has three meanings. A page uses the full phrase if the meaning isn't clear.

- **PostgreSQL schema:** a namespace in the database. PostgREST serves each source schema as a profile, which a request selects with `Accept-Profile`.
- **Configured schema:** an entry under `schemas` in `sync.json`. It has one source, one PostgreSQL schema and one catalog.
- **JSON schema:** a document that checks the shape of data, such as the `config` schema of the proxy.

## Acronyms

| Acronym | Expansion |
| --- | --- |
| CNPG | CloudNativePG, the operator that runs the PostgreSQL cluster. |
| CORS | cross-origin resource sharing. |
| DBOS | The library that runs the durable workflows. Its state lives in the `dbos` schema of PostgreSQL. |
| DSN | data source name, the connection string of a database. |
| JWT | JSON Web Token, the signed token that a client sends. |
| KEDA | Kubernetes Event-driven Autoscaling. |
| OTLP | OpenTelemetry Protocol. |
| PVC | PersistentVolumeClaim, a Kubernetes request for storage. |
| RBAC | role-based access control. |
| RLS | row-level security. |
| TTL | time to live. |

<!-- nav:start -->
---

[Previous: Data model](model.md) | [Next: Notebooks](notebooks.md) | [Home](index.md)
<!-- nav:end -->
