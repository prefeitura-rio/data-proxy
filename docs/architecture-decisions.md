# Architecture decisions

This doc exists so a future reader — human or agent — doesn't have to reverse-engineer *why*
this repo is built the way it is. Every non-obvious choice below traces back to a specific
constraint or a specific piece of prior research. If you're adapting this pattern to a new
project, start here.

## Why PostgREST instead of a hand-written API (Elixir/Phoenix, FastAPI, Hono, ...)

This is "Option A" of `aplications-architecture/proposta-pedro`. PostgREST turns the Postgres
schema itself into the REST contract: filtering, pagination, ordering, column selection, and
an OpenAPI spec all come for free from table/column definitions, with zero hand-written API
code. The tradeoff (documented in `proposta-pedro`'s own comparison table) is that the schema
*is* the contract — renaming a column breaks clients with no warning, and there's no
in-process cache. That's an accepted tradeoff for a PoC whose job is to validate the
pg_duckdb + sync + row-level-access pattern, not to be a production API. `proposta-pedro`
documents a trajectory from PostgREST to Elixir/Phoenix if/when the API needs a real
application layer (dynamic queries, response shaping, proactive caching) — that trajectory is
out of scope here.

## Why pg_duckdb instead of plain PostgreSQL

`pg_duckdb` embeds DuckDB's columnar engine inside Postgres, and this PoC uses it specifically
in `scripts/sync.py` to read Parquet mart output from GCS efficiently — DuckDB's columnar scan
is materially cheaper for that than round-tripping through a separate ETL tool. See
`aplications-architecture/proposta-pedro` for the full reasoning behind reading dbt marts
straight from Parquet.

**Important correction from the original plan**: pg_duckdb does *not* serve requests directly
from `USING duckdb` columnar tables in this architecture. Phase 3 confirmed (see
`docs/phase-3-sync-findings.md`) that DuckDB execution bypasses the Postgres executor entirely,
which means Row-Level Security — the mechanism this repo relies on for row-level access control
(see below) — cannot apply to a DuckDB-execution query path at all. Persistent `USING duckdb`
tables also aren't available self-hosted without MotherDuck (a paid service). So pg_duckdb's
role here is confined to the offline sync step; `api.citizens` / `api.service_records` are
plain Postgres heap tables, and that is the permanent design, not a temporary fallback.

## Why Row-Level Security instead of delegating access control to the cluster

`proposta-pedro` states the environment constraint as: "Authentication, authorization and rate
limiting are the Kubernetes cluster's responsibility; the API trusts the identity injected via
headers." Taken literally, that would mean PostgREST (with no code of its own) has no way to
filter rows by the caller's authorized units — the cluster can prove *who* is calling, but it
has no mechanism to inject a *SQL filter clause*.

`aplications-architecture/comparacao-propostas-arquitetura.md` (the internal review comparing
four competing proposals for this same problem) flags exactly this as Pedro's proposal's
biggest blind spot: *"delegating row-level security to the cluster is insufficient... would
need RLS in the database or an application layer."*

This repo resolves that gap without contradicting the original constraint: the cluster still
owns authentication (nothing here verifies a JWT), but the *identity it already established* is
carried into the database as a header, and Postgres RLS does the row filtering. See
"Row-level access control" in the main `README.md` for the mechanism, and
`docs/phase-1-validation.md` for the PostgREST-version-specific gotcha discovered while wiring
it up.

## Why a plain script for sync, not Prefect

Production `proposta-pedro` explicitly names Prefect as the sync orchestrator (webhook after
dbt run, primary trigger). For this PoC specifically, adding a full Prefect server+worker to
`docker-compose` would be a second orchestration system to run and understand for a pattern
that, at PoC scale, is one script doing three steps (BQ query -> GCS Parquet -> pg_duckdb
load). This was an explicit scope decision for the PoC, not a claim that Prefect is wrong for
production — whoever adapts this pattern to a real always-on service should look at
`proposta-pedro`'s Prefect-based trigger design, not this repo's manual `just sync`.

## Why docker-compose only, no Kubernetes

Explicit scope decision: this PoC needs to be cloneable and runnable by anyone on the team
without cluster access, GCP IAM for a GKE service account, Cloud SQL Proxy setup, or any
k8s manifests. The tradeoff is real and worth naming: production `pg_duckdb` would need to run
self-managed (Cloud SQL doesn't support third-party Postgres extensions), most likely as a
StatefulSet, which introduces PVC/backup/monitoring concerns this PoC does not exercise. If
this pattern moves toward production, that operational surface needs its own design pass.

## Why a synthetic dataset instead of real app-pic data

Keeps this repo fully decoupled from app-pic's real BigQuery project, credentials, and PII.
The synthetic schema (`citizens` + `service_records`, both keyed by `unit_id`) is deliberately
shaped to be analogous to a real gov app's governance model (e.g. app-pic's CRAS/secretaria
access control, see `app-pic/src/utils/secretaria_access.py`) without using real data or a
real BQ project. See `docs/phase-7-handoff.md` (once written) for the explicit mapping between
this PoC's `unit_id` pattern and app-pic's actual governance fields.

## What this repo does not claim

- It does not claim PostgREST-only APIs are sufficient for every app in this problem class —
  `proposta-pedro` itself documents when to graduate to Elixir/Phoenix.
- It does not claim pg_duckdb is production-ready without further validation — see the open
  items in `docs/phase-1-validation.md`.
- It does not recommend app-pic adopt this architecture over its current in-flight
  Hono/Prisma/PostgreSQL migration (`app-pic/MIGRATION.md`). That's a decision for the team;
  this repo exists to make that decision better-informed, not to make it.
