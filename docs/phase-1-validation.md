# Phase 1 — pg_duckdb + PostgREST + RLS validation spike

**Status: PASSED.** No incompatibility found between pg_duckdb, PostgREST, and Postgres RLS.
The architecture in `proposta-pedro` (Option A) plus the RLS fix in this repo's README is
confirmed viable. This doc records exactly what was tested and the one real gotcha found, so
nobody has to re-discover it.

## What was tested

Fresh boot (`docker-compose up -d` from a clean volume — no manual DB patching), then:

1. **Extension loads correctly**: `CREATE EXTENSION pg_duckdb` via `db/init/01_extensions.sql`
   runs cleanly on container init. `\dx` shows `pg_duckdb 1.1.0`.
2. **PostgREST introspects pg_duckdb-adjacent schema correctly.** Note: the `api.citizens` /
   `api.service_records` tables in this PoC are plain Postgres heap tables (not DuckDB-backed
   columnar tables) — RLS and pg_duckdb columnar storage were validated **separately**, not
   simultaneously on the same table, because RLS predicates and DuckDB's columnar storage
   engine are two independent pg_duckdb capabilities and mixing them in the first spike would
   have made a failure ambiguous to diagnose. See "What's still open" below.
3. **Schema cache**: PostgREST loaded `2 Relations, 2 Relationships, 1 Functions` on boot,
   confirming FK-based resource embedding (`service_records.citizen_id -> citizens.id`) is
   picked up automatically — no manual view/config needed.
4. **RLS end-to-end, fresh boot, zero manual steps**:
   - No `X-User-Units` header → `[]` (fails closed — correct security default)
   - `X-User-Units: cras_1` → only rows where `unit_id = 'cras_1'`
   - `X-User-Units: cras_1,cras_2` → union of both
   - Filtering (`?status=eq.ativo`) composes correctly with RLS (both apply, AND'ed)
   - Pagination (`Range: 0-0` header) composes correctly with RLS
   - Resource embedding (`?select=protocol_type,citizens(name)`) respects RLS on the embedded
     side too — a citizen outside the caller's `X-User-Units` does not leak through a join

## The one real gotcha: PostgREST v12 header GUC convention changed

**Symptom during testing:** the `pre_request()` function was written using
`current_setting('request.header.x-user-units', true)` (the "one GUC per header" form
documented in a lot of PostgREST tutorials/blog posts). It silently returned `NULL` for every
request — no error, just empty results, which looked exactly like an RLS misconfiguration
until isolated.

**Root cause:** that per-header GUC convention (`request.header.<name>`) is from an **older**
PostgREST version. **PostgREST v12.2.8** (the version pinned in this repo's
`docker-compose.yml`) only exposes the full header set as a single JSON blob under
`current_setting('request.headers', true)`. Confirmed by adding a temporary debug RPC that
dumped `request.headers` directly — it returned a valid JSON object with all headers
(`{"x-user-units":"cras_1", "host":"...", ...}"`), proving PostgREST *was* forwarding the
header correctly the whole time; only the extraction syntax in `pre_request()` was wrong for
this version.

**Fix**, now in `db/init/03_pre_request.sql`:
```sql
current_setting('request.headers', true)::json ->> 'x-user-units'
```
instead of:
```sql
current_setting('request.header.x-user-units', true)  -- WRONG for PostgREST v12
```

**Lesson for whoever adapts this pattern elsewhere:** always verify which GUC convention your
pinned PostgREST version actually uses, with a throwaway debug RPC dumping
`current_setting('request.headers', true)` directly, before assuming either form works. Don't
trust tutorials without checking the version they were written against.

## What's still open (deferred, not blocking)

- ~~RLS directly on a `duckdb`-access-method table~~ **RESOLVED in Phase 3, see
  `docs/phase-3-sync-findings.md`.** Short version: RLS and DuckDB execution do not compose —
  DuckDB execution bypasses the Postgres executor (and its RLS layer) entirely, and persistent
  `USING duckdb` tables aren't even available self-hosted without MotherDuck. The fallback this
  doc anticipated is the one that ended up being correct: `api.citizens`/`api.service_records`
  stay plain heap tables; pg_duckdb/DuckDB execution is confined to `scripts/sync.py`'s offline
  batch step only.
- Performance under 50+ authorized units per caller was not load-tested in this phase (index
  exists — `citizens_unit_id_idx`, `service_records_unit_id_idx` — but no benchmark run yet).

## How to reproduce this validation

```bash
just reset   # wipe volume, fresh start
just up
docker exec poc-pg-duckdb-postgrest-db-1 psql -U poc -d poc -c "
  INSERT INTO api.citizens (name, unit_id, status) VALUES
    ('Alice', 'cras_1', 'ativo'), ('Bob', 'cras_2', 'ativo'), ('Carol', 'cras_1', 'inativo');
"
curl -s http://localhost:3111/citizens -H "X-User-Units: cras_1"   # -> Alice, Carol
curl -s http://localhost:3111/citizens -H "X-User-Units: cras_2"   # -> Bob
curl -s http://localhost:3111/citizens                              # -> []
```
