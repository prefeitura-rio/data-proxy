# Handoff notes for app-pic

**This is not a recommendation to replace app-pic's in-flight migration plan
(`app-pic/MIGRATION.md`: Hono + Prisma + Cloud SQL PostgreSQL).** That plan is already decided,
scoped, and has execution steps written. This document exists so the app-pic team can pull out
the specific, load-bearing findings from this PoC that are relevant to decisions they still
have open in that plan — mainly around row-level governance and BQ→Postgres sync — without
having to read this whole repo.

## What's directly relevant to app-pic's plan

### 1. Row-level governance: RLS vs. app-layer `WHERE id_cras = ANY($list)`

`MIGRATION.md`'s Etapa 4 plan is:
```
GET /participants (listagem com filtros + paginação SQL)
Governança em SQL (WHERE id_cras = ANY($list))
⚠️  Testar exaustivamente: quem vê o quê deve ser idêntico ao comportamento atual
```

That `$list` is presumably built in the Hono handler from the caller's authorized CRAS units,
then interpolated as a query parameter into every handler that touches `participants`. This
works, but it means **every current and future endpoint that queries `participants` must
remember to add that clause** — there is no structural enforcement if a new endpoint (or a
future contributor) forgets it. That `⚠️` in the plan is flagging exactly this risk.

This PoC's Postgres RLS policies (`db/init/04_schema.sql`) are the same governance rule
(`unit_id = ANY(string_to_array(...))`) enforced **at the table level**, so it's structurally
impossible to bypass without superuser access — confirmed in `docs/phase-5-rls-e2e-validation.md`
(no header → empty result, not an error; a raw `DELETE` still gets rejected by grants; embedded
joins don't leak rows from unauthorized units either). If app-pic's Prisma/PostgreSQL layer
adopts RLS instead of (or in addition to) the app-layer `WHERE` clause, the same governance
holds even if a future handler forgets to filter explicitly — the database enforces it either
way.

**Adapting this to Prisma specifically**: Prisma's query engine doesn't set custom session
GUCs per-request by default the way this PoC's `PGRST_DB_PRE_REQUEST` does. The equivalent
would be running `SELECT set_config('app.user_units', $1, true)` (or similar) inside the same
transaction as each Prisma query — e.g. via `prisma.$transaction([...])` or a middleware that
wraps every request in a raw `SET LOCAL` statement before the Prisma calls. This is a real
integration detail to work out, not a drop-in — but the underlying RLS policy SQL in
`db/init/04_schema.sql` is directly reusable as-is against a `participants`/`id_cras` shaped
schema.

### 2. BigQuery → Postgres sync gotchas (relevant regardless of sync tooling choice)

`MIGRATION.md`'s Etapa 2 needs a "Sync script: BQ → PG". This PoC's `scripts/sync.py` hit three
gotchas worth knowing before writing that script, documented in full in
`docs/phase-3-sync-findings.md`:

- **If using pg_duckdb for the sync step** (reading BQ-exported Parquet efficiently): DuckDB
  execution cannot write directly into an existing Postgres table
  (`INSERT INTO x SELECT FROM read_parquet(...)` fails) — materialize into a `CREATE TEMP TABLE
  ... AS SELECT` first, then a plain `INSERT ... SELECT` from that temp table into the real
  table. And critically: **do not attempt to serve requests directly from a `USING duckdb`
  table with RLS** — DuckDB execution bypasses the Postgres executor (and RLS) entirely. Confine
  pg_duckdb strictly to the batch sync step if you use it at all.
- **If using a plain BQ client library instead** (Python `google-cloud-bigquery`, Node
  `@google-cloud/bigquery`, or the TypeScript sync sketched in `MIGRATION.md`'s "Evolução de
  schema" section): none of the pg_duckdb-specific gotchas above apply — you're doing a normal
  row-by-row or batch upsert, which is closer to what `MIGRATION.md` already sketches with
  Prisma's `upsert()`. This PoC's finding is mainly a warning against the *pg_duckdb-mediated*
  sync path, not a case against BQ→PG sync generally.
- **FK-referenced tables need combined `TRUNCATE`** if doing full-refresh sync (not incremental):
  `TRUNCATE table_a, table_b` in one statement, not two separate statements, when an FK exists
  between them — this applies regardless of what's doing the truncating.

### 3. Schema-as-contract (PostgREST) is explicitly NOT what app-pic is adopting — and that's fine

This PoC's headline pattern (Postgres schema *is* the REST API, via PostgREST, zero
hand-written endpoint code) is **not relevant** to app-pic's plan, which is building a
hand-written Hono API on purpose — for good reasons specific to app-pic's needs (custom
business logic in `/participants`, `/dashboard`, `/admin` endpoints; a unified TypeScript
stack with the Next.js frontend). Nothing here is meant to second-guess that choice. This
section exists only to be explicit that the "no hand-written API" angle of this PoC doesn't
transfer, so nobody mistakes this repo as suggesting a rewrite of the API layer.

## What this PoC does not tell you

- **No load testing.** All validation here is against 500/1219-row synthetic tables. App-pic's
  real `participants` table is ~168k rows per `MIGRATION.md`. RLS's `= ANY(array)` predicate is
  indexed here (`citizens_unit_id_idx`) and should scale similarly on `id_cras`, but this was
  never benchmarked at app-pic's real volume.
- **No Cloud SQL specifics.** This PoC runs pg_duckdb (a self-hosted Postgres extension) via
  docker-compose, not Cloud SQL. RLS itself is standard PostgreSQL 15+ (Cloud SQL Postgres 17
  supports it natively, per `MIGRATION.md`'s planned version) — the RLS mechanism should carry
  over unchanged, but pg_duckdb specifically (and its sync-step-only role) has no bearing on
  Cloud SQL, since app-pic's plan doesn't use pg_duckdb at all.
- **No Prisma-specific implementation was built or tested here.** The "how to set the RLS
  session variable per-request under Prisma" question above is a real open item, not solved by
  this repo — it's PostgREST's `PGRST_DB_PRE_REQUEST` that does this here, and Prisma has no
  direct equivalent.

## Suggested next step, if app-pic wants to explore RLS

A small, low-risk spike: add a single RLS policy to the `participants` table in a
non-production Cloud SQL instance (or even local Postgres via Prisma's dev setup), matching
this PoC's `unit_id = ANY(string_to_array(...))` shape but for `id_cras`, and confirm a Prisma
middleware can set the session variable per-request before running a query. If that works,
Etapa 4's `⚠️  Testar exaustivamente` risk is meaningfully reduced — the governance check moves
from "must remember to write correctly in every handler" to "database enforces it structurally,
tested once."
