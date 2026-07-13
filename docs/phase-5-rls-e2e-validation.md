# Phase 5 — RLS end-to-end validation against real synced data

**Status: PASSED.** This is the validation that matters most for this PoC's stated purpose:
closing the row-level-access-control gap `comparacao-propostas-arquitetura.md` flagged in
`proposta-pedro`. Phase 1 proved the RLS *mechanism* works against a handful of hand-inserted
rows; this phase proves it holds correctly against the full real dataset synced from BigQuery
in Phase 3 (500 citizens / 1219 service_records across 5 units), including checks Phase 1
didn't cover.

## What was tested

1. **Per-unit counts match ground truth exactly.** Superuser (RLS-bypassing) counts per unit —
   cras_1=97, cras_2=101, cras_3=94, cras_4=100, cras_5=108 — match PostgREST's RLS-filtered
   response for each unit exactly, one at a time.
2. **Union correctness.** Requesting all 5 units returns exactly 500 citizens / 1219
   service_records (the full dataset); requesting 2 units (`cras_1,cras_2`) returns exactly
   198 (97+101) — RLS's `ANY(string_to_array(...))` correctly unions authorized units rather
   than silently dropping rows or double-counting.
3. **Fails closed, not just on empty header.** Three distinct "should return nothing" cases,
   all correctly return `200 []` (not an error, not a leak):
   - No `X-User-Units` header at all.
   - Header present but naming a unit that doesn't exist in the data (`cras_99`).
   - Header containing a SQL-injection-style payload
     (`cras_1'; DROP TABLE api.citizens; --`) — confirmed harmless and the table is
     untouched, because `api.pre_request()` passes the header value into `set_config()` as a
     parameter, never string-concatenated into SQL, and RLS's `string_to_array` treats the
     whole malformed string as one non-matching unit name rather than executing it.
4. **Writes are rejected, not just filtered.** `POST`/`DELETE` against `/citizens` both return
   `401 permission denied for table citizens` (Postgres error code `42501`) — `web_anon` only
   ever received `GRANT SELECT`, so this PoC is enforced read-only at the grant level, not just
   by convention.
5. **No cross-unit leakage through resource embedding.** For every `service_records` row
   visible to a `cras_1`-scoped caller, embedding the related `citizens` row never returns a
   citizen from a different unit (checked over 100 embedded rows, zero leaks). This is the
   specific failure mode row-level security is meant to prevent in a joined/embedded query —
   PostgREST does not run embeds as a privileged escape hatch around RLS.

## Why this matters for app-pic / future migrations

This is the concrete answer to the gap flagged in
`aplications-architecture/comparacao-propostas-arquitetura.md`: *"delegating row-level
security to the cluster is insufficient... would need RLS in the database or an application
layer."* Here, the cluster still owns authentication (this PoC never verifies a JWT — see
`README.md`, "Row-level access control"), but authorization down to individual rows is fully
enforced in Postgres via RLS, fed only by the identity the cluster already established. No
application code, in PostgREST or otherwise, is trusted to remember to add a `WHERE unit_id IN
(...)` clause — it is structurally impossible to bypass without direct database access.

## How to reproduce

```bash
just reset && just up && just sync
export UNITS="cras_1,cras_2,cras_3,cras_4,cras_5"

# Ground truth (bypasses RLS)
docker exec poc-pg-duckdb-postgrest-db-1 psql -U poc -d poc -c \
  "SELECT unit_id, count(*) FROM api.citizens GROUP BY unit_id;"

# Fails closed
curl -s http://localhost:3111/citizens                              # -> []
curl -s http://localhost:3111/citizens -H "X-User-Units: cras_99"    # -> []

# Per-unit / union correctness
curl -s http://localhost:3111/citizens -H "X-User-Units: cras_1" | jq length
curl -s http://localhost:3111/citizens -H "X-User-Units: $UNITS" | jq length   # -> 500

# Writes rejected
curl -s -X DELETE "http://localhost:3111/citizens?unit_id=eq.cras_1" -H "X-User-Units: cras_1"
# -> 401 permission denied for table citizens

# No cross-unit leak via embedding
curl -s "http://localhost:3111/service_records?unit_id=eq.cras_1&select=id,citizens(unit_id)" \
  -H "X-User-Units: cras_1" | jq '[.[] | select(.citizens.unit_id != "cras_1")] | length'
# -> 0
```
