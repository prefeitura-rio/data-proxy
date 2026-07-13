# Phase 4 — PostgREST exposure validation (real synced data)

**Status: PASSED.** All standard PostgREST capabilities validated against the real
BigQuery-synced data from Phase 3 (500 citizens, 1219 service_records) — no hand-written API
code involved anywhere; every result below comes purely from PostgREST introspecting the
Postgres schema.

## What was tested

1. **OpenAPI / schema-as-contract**: `GET /` returns a Swagger 2.0 document listing `/citizens`,
   `/service_records`, and `/rpc/pre_request`. The `definitions` section reflects the exact
   Postgres schema automatically: `citizens`/`service_records` column types, the PK on `id`,
   and the FK from `service_records.citizen_id` to `citizens.id` — all derived purely from
   `db/init/04_schema.sql`, nothing hand-maintained.
2. **Filtering**: `?status=eq.ativo` and `?unit_id=in.(cras_1,cras_2)` both compose correctly
   with RLS (RLS narrows to the caller's authorized units first; the filter then narrows
   further within that set — confirmed counts are a strict subset of the unfiltered RLS
   result).
3. **Pagination**: both mechanisms work —
   - `Range: 0-9` / `Range-Unit: items` headers → `Content-Range: 0-9/*` response header.
   - `?limit=5&offset=10` query params → exactly 5 rows returned.
4. **Ordering**: `?order=name.desc` returns rows in the expected descending order.
5. **Resource embedding**: already covered in Phase 1 against a plain heap table with mock
   data; re-confirmed here against the real synced 1219-row `service_records` /
   500-row `citizens` dataset via
   `?select=protocol_type,citizens(name)` — RLS applies correctly on the embedded side too.

## Nothing new to report

No gotchas or surprises this phase — Phase 1 already validated the RLS+PostgREST composition
mechanics using a plain heap table with a handful of manually-inserted rows; Phase 4 confirms
the exact same behavior holds at real data volume (500/1219 rows) sourced from the real
BigQuery -> GCS -> pg_duckdb -> heap-table pipeline built in Phase 3, not just hand-typed test
fixtures. This is the expected outcome, not a new finding — recorded here mainly so a future
reader doesn't have to re-run these checks to confirm PostgREST's behavior is stable once real
data volume and a real sync pipeline are in the loop.

## How to reproduce

```bash
just reset && just up
just sync              # BigQuery -> GCS Parquet -> pg_duckdb -> heap tables
export UNITS="cras_1,cras_2,cras_3,cras_4,cras_5"

curl -s http://localhost:3111/ | jq '.paths, .definitions.citizens'
curl -s "http://localhost:3111/citizens?status=eq.ativo" -H "X-User-Units: $UNITS" | jq length
curl -s "http://localhost:3111/citizens?limit=5&offset=10" -H "X-User-Units: $UNITS" | jq length
curl -s -i "http://localhost:3111/citizens" -H "X-User-Units: $UNITS" -H "Range: 0-9" -H "Range-Unit: items" | grep -i content-range
curl -s "http://localhost:3111/service_records?select=protocol_type,citizens(name)&limit=3" -H "X-User-Units: cras_1" | jq .
```
