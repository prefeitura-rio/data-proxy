# Phase 3 — sync script findings: RLS vs. DuckDB execution

**This resolves the "still open" item from `docs/phase-1-validation.md`** ("RLS specifically on
`USING duckdb` columnar tables was not yet independently re-confirmed"). The answer is
definitive: **RLS and DuckDB execution do not compose, by design, in pg_duckdb's current
architecture.** This is not a bug to work around — it's a hard boundary the sync script and
schema are built around.

## The core finding

DuckDB execution (any query touching `read_parquet()`, `duckdb.create_simple_secret()`, or a
`USING duckdb` table) is handled entirely by DuckDB's own execution engine, which **bypasses
the Postgres executor completely** — and Row-Level Security is a Postgres executor feature.
There is no configuration that makes RLS apply to a DuckDB-execution query path. This was
independently confirmed two ways in this repo:

1. **Live testing**: granting `web_anon` `duckdb.postgres_role` + `GRANT USAGE ON FOREIGN DATA
   WRAPPER duckdb` got past permission errors, but GCS secrets created by a superuser session
   were never visible to `web_anon`'s session — DuckDB secrets are scoped per-session/per-role
   in a way that doesn't share cleanly across roles, and even if it did, RLS still wouldn't
   apply to the resulting query since DuckDB execution ignores it entirely.
2. **Oracle consultation** (architecture review, not code): confirmed the same conclusion
   independently — "DuckDB execution completely bypasses Postgres's executor and RLS
   enforcement layer... there's no opportunity for RLS policies to take effect."

Also confirmed live: `CREATE TABLE ... USING duckdb` (persistent DuckDB-storage tables) is
**not available in self-managed pg_duckdb at all** — it errors with `"Only TEMP tables are
supported in DuckDB if MotherDuck support is not enabled"`. Persistent columnar DuckDB storage
requires MotherDuck (a paid cloud service), not just the open-source pg_duckdb extension. This
is a second, independent reason the originally-imagined "pg_duckdb columnar tables serving
requests directly" design isn't viable self-hosted, on top of the RLS incompatibility.

## The resolved architecture

**pg_duckdb / DuckDB execution is confined entirely to `scripts/sync.py`'s offline batch step,
run as the privileged `poc` role. Every table RLS and PostgREST touch is a plain Postgres heap
table.** `web_anon` (the PostgREST-facing role) has zero DuckDB or FDW grants — it never needs
any, since it never runs a DuckDB-execution query.

This means "pg_duckdb" in this PoC's name refers to its role in the **sync/ETL step**
(reading Parquet from GCS efficiently, with DuckDB's columnar engine doing that read), not to
storing the serving tables in DuckDB's own columnar format. `proposta-pedro`'s core value
proposition — dbt marts read cheaply from Parquet without a separate ETL tool — is still fully
realized. What's different from the original mental model is *where* the columnar engine's
work ends: at the sync step, not carried through into request-serving.

## The INSERT-SELECT gotcha this surfaced

Trying the "obvious" sync implementation —
```sql
INSERT INTO api.citizens SELECT * FROM read_parquet('gs://...')
```
fails with `DuckDB does not support modifying Postgres tables`. DuckDB execution can **create**
a brand new table from a scan (`CREATE TABLE ... AS SELECT`), but cannot **write into** an
existing Postgres heap table directly. The fix, used in `scripts/sync.py`:

```sql
-- Step 1: DuckDB execution creates a new (session-local TEMP) table.
CREATE TEMP TABLE tmp_citizens AS
  SELECT r['id']::uuid AS id, r['name']::text AS name, ...
  FROM read_parquet('gs://...') AS r;

-- Step 2: plain Postgres-to-Postgres INSERT-SELECT, zero DuckDB execution.
INSERT INTO api.citizens (id, name, ...) SELECT id, name, ... FROM tmp_citizens;
```

Two smaller gotchas hit while building this, both now commented inline in `scripts/sync.py`:

- **Composite row type**: `read_parquet()` returns rows of a composite `row` type. Columns
  must be pulled out via `r['colname']::type` with an alias (`r['id']::uuid AS id`) — plain
  `SELECT *` or bare column references fail with `column "id" does not exist` (Postgres's own
  error message points at the correct `r['colname']` syntax).
- **psycopg parameterization**: passing the GCS URI as a bind parameter
  (`read_parquet(%s)`) to `CREATE TABLE ... AS SELECT` breaks pg_duckdb's type inference with
  `Could not convert DuckDB type: UNKNOWN to Postgres type`, even with an explicit `::text`
  cast on the parameter. Literal string interpolation works. Verified this is safe in
  `scripts/sync.py` — the URI is built entirely from our own constants, never external input,
  with an assertion guarding against anything unexpected slipping in.
- **TRUNCATE + FK**: Postgres refuses to `TRUNCATE` a table referenced by an FK
  (`api.citizens`, referenced by `api.service_records`) unless every referencing table is
  truncated in the *same statement* — truncating them as separate statements fails even when
  done in the correct dependency order.

## What this means for adapting this pattern elsewhere

If you're building a similar analytical-serving-layer app: pg_duckdb is a legitimate, working
choice for the **sync/ETL step reading columnar Parquet from cloud storage**. Do not expect to
serve requests directly from `USING duckdb` tables with RLS — that combination isn't supported
by pg_duckdb's current architecture (self-hosted, non-MotherDuck) at all. Plan for two distinct
layers from the start: a pg_duckdb-powered batch sync job (superuser-ish privileges, no
per-request RLS concerns) writing into ordinary Postgres heap tables, which PostgREST and RLS
then serve with zero DuckDB involvement.
