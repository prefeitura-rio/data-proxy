# Thresholds

These sections give the performance thresholds of the load test and the limits of a single generator.

## Performance thresholds

A normal load run gates these values: `checks`, HTTP failure rate, dropped iterations, cache latency, bounded DuckLake latency and fallback latency. It also gates the latency of heavy, selective and pinned reads. [k6 commands](index.md) defines these terms.

It always confirms `dropped_iterations == 0` so a run that can't start an iteration fails. The bounded DuckLake and cache trends carry a `p(99)` bound in addition to `p(95)`. Add `--report-only` when the client-risk route trends should be reported without gating the run. The flag sets `K6_GATE_CLIENT_RISK=false`. Direct k6 runs can set that variable explicitly.

## Capacity runs need a separate generator

A single-node cluster can't measure its own capacity above a few hundred requests per second.

The generator, PostgREST, PostgreSQL, PgDog, and the proxy share the node's cores. The generator therefore steals CPU from the system under test, and the run reports a number that belongs to neither.

Measure capacity with the generator outside the cluster host. The `default` devenv profile provides `k6`, and every address the suite needs comes from the environment:

```bash
kubectl -n istio-ingress port-forward svc/istio-ingressgateway 3111:80 &
kubectl -n keycloak port-forward svc/keycloak 8080:8080 &
kubectl -n data-proxy port-forward svc/data-proxy-postgrest 3000:3000 &

BASE_URL=http://localhost:3111 \
API_HOST=data-proxy.local \
OIDC_TOKEN_URL=http://localhost:8080/realms/dev/protocol/openid-connect/token \
K6_PROFILE=load K6_PEAK_RATE=500 POSTGREST_URL=http://localhost:3000 \
k6 run k6/load.js --summary-export=summary.json
```

The suite reads the same `K6_PROFILE`, `K6_PEAK_RATE`, `K6_FALLBACK_SHARE`, and `K6_BOTTLENECK_SHARE` variables as the in-cluster runner. Within the cluster, the runner pod now requests CPU and memory. A starved generator then fails its own probes instead of shaping the result without notice. A run that drops iterations fails the `dropped_iterations` threshold, which marks the measurement invalid rather than a capacity limit.

Run `cluster k6 e2e` after changing local images, sync configuration, fallback, SeaweedFS, or pg_duckdb behavior.

<!-- nav:start -->
---

[Previous: k6 commands](index.md) | [Next: E2E layout](e2e.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
