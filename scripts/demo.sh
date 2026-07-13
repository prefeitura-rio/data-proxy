#!/usr/bin/env bash
# Runs the checks documented in docs/phase-4-postgrest-validation.md and
# docs/phase-5-rls-e2e-validation.md against a running stack with synced
# data, so a reviewer can watch the RLS/PostgREST behavior live instead of
# re-typing curl commands from the docs by hand. Assumes `just up` and
# `just sync` have already run. Requires `jq`.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:3111}"
UNITS_ALL="cras_1,cras_2,cras_3,cras_4,cras_5"
PASS=0
FAIL=0

check() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  OK   $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL $desc (expected $expected, got $actual)"
    FAIL=$((FAIL + 1))
  fi
}

echo "== OpenAPI / schema-as-contract =="
curl -s "$BASE_URL/" | jq '.paths | keys'

echo ""
echo "== Fails closed =="
n=$(curl -s "$BASE_URL/citizens" | jq length)
check "no header -> zero rows" "0" "$n"

n=$(curl -s "$BASE_URL/citizens" -H "X-User-Units: cras_99" | jq length)
check "unauthorized unit -> zero rows" "0" "$n"

echo ""
echo "== Per-unit RLS matches full dataset =="
total=$(curl -s "$BASE_URL/citizens" -H "X-User-Units: $UNITS_ALL" | jq length)
check "all 5 units -> full 500-citizen dataset" "500" "$total"

sr_total=$(curl -s "$BASE_URL/service_records" -H "X-User-Units: $UNITS_ALL" | jq length)
check "all 5 units -> full 1219-service_records dataset" "1219" "$sr_total"

c1=$(curl -s "$BASE_URL/citizens" -H "X-User-Units: cras_1" | jq length)
c2=$(curl -s "$BASE_URL/citizens" -H "X-User-Units: cras_2" | jq length)
union=$(curl -s "$BASE_URL/citizens" -H "X-User-Units: cras_1,cras_2" | jq length)
check "cras_1+cras_2 union == cras_1 count + cras_2 count" "$((c1 + c2))" "$union"

echo ""
echo "== Filtering + pagination compose with RLS =="
ativo=$(curl -s "$BASE_URL/citizens?status=eq.ativo" -H "X-User-Units: $UNITS_ALL" | jq length)
echo "  INFO ativo citizens across all units: $ativo (informational, not a fixed expectation)"

paged=$(curl -s "$BASE_URL/citizens?limit=5&offset=10" -H "X-User-Units: $UNITS_ALL" | jq length)
check "limit=5 returns exactly 5 rows" "5" "$paged"

echo ""
echo "== Writes rejected (SELECT-only grant) =="
code=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$BASE_URL/citizens?unit_id=eq.cras_1" -H "X-User-Units: cras_1")
check "DELETE returns 401 permission denied" "401" "$code"

echo ""
echo "== No cross-unit leak through resource embedding =="
leaks=$(curl -s "$BASE_URL/service_records?unit_id=eq.cras_1&select=id,citizens(unit_id)" \
  -H "X-User-Units: cras_1" | jq '[.[] | select(.citizens.unit_id != "cras_1")] | length')
check "zero cross-unit citizens leaked via embed" "0" "$leaks"

echo ""
echo "================================"
echo "  $PASS passed, $FAIL failed"
echo "================================"
[ "$FAIL" -eq 0 ]
