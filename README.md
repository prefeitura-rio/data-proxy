# poc-pg-duckdb-postgrest

Proof of concept implementing **Option A** of
[`aplications-architecture/proposta-pedro`](https://github.com/prefeitura-rio/aplications-architecture/blob/master/proposta-pedro/README.md):
a read-only serving layer for BigQuery/dbt marts, backed by
[pg_duckdb](https://github.com/duckdb/pg_duckdb) (PostgreSQL + embedded DuckDB columnar engine)
and exposed via [PostgREST](https://postgrest.org) — no hand-written API code, the Postgres
schema *is* the REST contract.

This PoC also closes a gap the workspace's own
[`comparacao-propostas-arquitetura.md`](https://github.com/prefeitura-rio/aplications-architecture)
review flagged in proposta-pedro: "delegating row-level security to the cluster is
insufficient — the cluster can verify identity but has no way to inject a SQL filter clause."
Here, row-level access control is enforced with Postgres Row-Level Security (RLS) policies,
fed by HTTP headers that the cluster injects after validating identity upstream.

**What this is not:** a replacement for [app-pic](https://github.com/prefeitura-rio/app-pic)'s
current in-flight migration (Hono/Prisma/PostgreSQL, see `app-pic/MIGRATION.md`). This PoC
exists to give the app-pic team (and any future BigQuery-serving app) a concrete, working
reference to evaluate against that path — not a recommendation of which to pick.

**Scope:** runs entirely via `docker-compose`. No Kubernetes, no Cloud SQL, no Prefect. The
only real external dependency is BigQuery + GCS in the existing sandbox project
`rj-iplanrio-dev`, hit by a plain, manually-triggered sync script.

## Architecture

```
BigQuery (synthetic dataset, rj-iplanrio-dev)
   │  scripts/sync.py: extract
   ▼
GCS Parquet
   │  scripts/sync.py: read_parquet('gs://...') via pg_duckdb, superuser-only
   ▼
Postgres heap tables (api.citizens, api.service_records)  ──  schema = contract
   │
   ▼
PostgREST  ──HTTP headers (X-User-Units, ...)──▶  RLS policies filter rows
   │
   ▼
Clients (curl / any HTTP client)
```

pg_duckdb's DuckDB execution engine is used only inside `scripts/sync.py`'s batch load step —
it cannot serve requests directly, since DuckDB execution bypasses the Postgres executor
entirely and RLS can't apply to it. See `docs/phase-3-sync-findings.md` for the full finding.
`api.citizens`/`api.service_records` are plain Postgres heap tables that RLS and PostgREST
serve with zero DuckDB involvement.

In production, `X-User-Units` and friends would be injected by the Kubernetes cluster's
ext_authz sidecar after validating a JWT. This PoC's demo scripts set those headers by hand to
simulate that trust boundary.

## Row-level access control

- PostgREST does **not** verify a JWT here — the cluster already did, and just forwards plain
  headers. PostgREST is configured to trust them (`PGRST_DB_PRE_REQUEST`).
- `api.pre_request()` runs once per request (same transaction as the query) and copies the
  `X-User-Units` header into a Postgres session variable via `set_config(..., true)` — no SQL
  string concatenation, no injection surface.
- RLS policies do `unit_id = ANY(string_to_array(current_setting('app.user_units', true), ','))`
  — an indexable `= ANY(array)` predicate (see `citizens_unit_id_idx` /
  `service_records_unit_id_idx`), so it stays fast even with 50+ authorized units per caller.
- A single `web_anon` role (`NOBYPASSRLS`, `SELECT`-only) serves every request — no per-request
  Postgres role switching, since there's no JWT claim to derive a role from.

See `db/init/02_roles.sql`, `db/init/03_pre_request.sql`, `db/init/04_schema.sql`.

## Running locally

```bash
just up          # starts pg_duckdb + PostgREST
just seed-bq      # one-time: creates the synthetic BQ dataset + tables
just sync         # BigQuery -> GCS Parquet -> pg_duckdb
just demo         # curl examples: filtering, pagination, RLS enforcement
```

PostgREST: http://localhost:3111 (OpenAPI spec at `GET /`)
Postgres: `localhost:5544` (user/pass/db default to `poc`/`poc`/`poc`, see `.env.example`)

## Documentation

- **`docs/architecture-decisions.md`** — the "why" behind every non-obvious choice in this
  repo (PostgREST vs. hand-written API, pg_duckdb vs. plain Postgres, RLS vs. cluster-only
  auth, docker-compose-only scope, synthetic data). Start here if you're adapting this pattern.
- **`docs/phase-1-validation.md`** — what was actually tested (pg_duckdb + PostgREST +
  RLS composability), the one real gotcha found (PostgREST v12's header GUC convention), and
  what's still open.
- **`db/init/*.sql`** — every file is commented inline explaining *why*, not just *what*.
  Read them in order (`01_extensions` → `02_roles` → `03_pre_request` → `04_schema`).

## Status

This PoC is being built in phases; see `.sisyphus/plans/poc-pedro-architecture.md` in the
`prefeitura-rio` workspace root for the full plan, decisions, and open risks.

- [x] Phase 0 — repo bootstrap
- [x] Phase 1 — local infra + pg_duckdb/PostgREST introspection validation spike ([findings](docs/phase-1-validation.md))
- [x] Phase 2 — synthetic dataset in BigQuery (`scripts/seed_bigquery.py`)
- [x] Phase 3 — sync script (BigQuery → GCS Parquet → pg_duckdb) ([findings](docs/phase-3-sync-findings.md))
- [ ] Phase 4 — PostgREST exposure (filtering/pagination/OpenAPI)
- [ ] Phase 5 — RLS wiring end-to-end test
- [ ] Phase 6 — demo & validation script
- [ ] Phase 7 — handoff notes for app-pic
