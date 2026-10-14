# Metrics

Sync workers send their metrics through OpenTelemetry (OTLP) when `OTLP_METRICS_ENDPOINT` is set.

## Worker metrics

| Metric                           | Labels             | Meaning                                                                   |
| -------------------------------- | ------------------ | ------------------------------------------------------------------------- |
| `dump_tasks_total`               | `table`, `status`  | Dump tasks. Status: `success` or `error`.                               |
| `dump_task_duration_seconds`     | `table`            | Dump duration.                                                            |
| `publish_tables_total`           | `schema`, `status` | Published tables. Status: `success` or `error`.                         |
| `publish_table_duration_seconds` | `table`            | Schema publication duration recorded for each published table.            |
| `seed_runs_total`                | `status`           | Seed runs. Current status: `success`.                                     |
| `sync_runs_total`                | `status`           | Sync runs. Status: `success` or `no_changes`.                             |

## Proxy logs

The proxy writes one text log line per request, for example `Proxy request completed: method=GET path=/people status=200 source=cache duration_ms=3 bytes=120`.

| Field         | Meaning                                      |
| ------------- | -------------------------------------------- |
| `method`      | HTTP method.                                 |
| `path`        | Request path.                                |
| `status`      | HTTP status.                                 |
| `source`      | `cache`, the `X-Source` value from PostgREST (`ducklake`, or `ducklake+<configured-fallback>`), `postgrest` when PostgREST sent none, or `none` when it was unreachable. |
| `duration_ms` | Request duration in milliseconds.            |
| `bytes`       | Response size.                               |

A request with `source=cache` was a cache hit. A repeated request that always shows another source means that the cache doesn't store the answer.

The cache is optional, so a Redis failure doesn't fail a request. The proxy writes the warning `Redis cache <connection|read|write> failed: <reason>` at most once every 30 seconds in each worker. Alert on this warning: without it, every request goes to PostgREST.

Use API response headers for client troubleshooting. Use logs for request analysis. See [Using the API](using.md#response-source-and-cache).

## PostgREST metrics

PostgREST serves Prometheus metrics at `/metrics` on its admin port, 3001 by default. The pod carries the `prometheus.io/*` scrape annotations. Set `postgrest.metrics.enabled` to `false` to turn the admin port off. The port is open to the pods in the cluster, and `/schema_cache` on it shows the schema structure.

| Metric                          | Meaning                                                                               |
| ------------------------------- | ------------------------------------------------------------------------------------- |
| `pgrst_db_pool_waiting`         | Requests that wait for a database connection. A value above 0 means that the pool is full. |
| `pgrst_db_pool_available`       | Free connections in the pool.                                                         |
| `pgrst_db_pool_timeouts_total`  | Requests that gave up after `dbPoolAcquisitionTimeout` seconds. A rising value means that clients get errors. |

Alert when `pgrst_db_pool_waiting` stays above 0, and when the timeouts counter rises. Both point to a database that needs more capacity, or to a request mix that's too heavy.

## Load profile limits

| Source                                     | Bound                         | Gates a run        |
| ------------------------------------------ | ----------------------------- | ------------------ |
| Cache                                      | p95 50 ms, p99 100 ms         | yes                |
| DuckLake bounded reads                     | p95 300 ms, p99 600 ms        | yes                |
| Configured fallback (`fallback`, `ducklake+<configured-fallback>`) | p95 6000 ms | no, client risk    |
| DuckLake heavy queries                     | p95 2000 ms, p99 5000 ms      | no, client risk    |
| DuckLake selective lookups                 | p95 2000 ms, p99 5000 ms      | no, client risk    |
| DuckLake pinned snapshots                  | p95 2000 ms, p99 5000 ms      | no, client risk    |

The load profile derives its rate from `K6_PEAK_RATE`, which defaults to 25 iterations per second. It ramps through 25%, 50%, 75%, and 100% of that peak, then holds the peak before ramping down. Use `cluster k6 load --peak-rate <n> --cache-ttl <seconds>` to select the target rate and proxy cache lifetime. The default load run uses the production-shaped 300-second cache TTL and gates fallback and heavy-route thresholds. Add `--report-only` for a run that records those routes without gating them.

The traffic mix is configurable with `--shares bottleneck=<fraction> fallback=<fraction>`. Normal load defaults to no bottleneck cases and 1% configured-fallback pairs. Add `--capacity` to use the wider capacity thresholds and extra VU headroom. The same single load scenario covers normal and capacity runs.

Because the arrival-rate executor counts iterations and a fallback pair makes two requests, the profile converts the target request rate into an iteration rate internally. The default load run gates fallback, heavy, selective, and pinned reads. Use `--report-only` when measuring a cold-cache stress run, for example `cluster k6 load --peak-rate 100 --cache-ttl 5 --report-only`.

## Capacity diagnosis

Check these signals during a load run. Compare pool waits with DuckLake and configured-fallback latency in the same PostgREST instance.

```sql
SELECT state, wait_event_type, wait_event, count(*)
FROM pg_stat_activity
WHERE usename = 'postgrest'
GROUP BY state, wait_event_type, wait_event
ORDER BY count(*) DESC;
```

```sql
SELECT calls, round(total_exec_time::numeric / 1000, 1) AS total_seconds,
       round(mean_exec_time::numeric, 1) AS mean_ms,
       left(regexp_replace(query, E'\\s+', ' ', 'g'), 160) AS query
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 20;
```

One PostgREST and one PgDog serve both DuckLake and configured fallback reads. The proxy logs separate cache hits, DuckLake reads, and configured fallback reads. A rising `duration_ms` p95 for DuckLake reads with low PostgreSQL execution time indicates pool wait or request queueing. A rising fallback trend indicates external-source latency. Keep the local k3d result as a comparison only: the load generator and services share one host.

## Scraping

CNPG exposes metrics on port 9187. SigNoz should scrape the CNPG pod metrics endpoint through its OpenTelemetry Collector. Select CNPG pods by their cluster labels and scrape the named `metrics` port.

The external Redis platform owns the Redis exporter and its metrics endpoint. The data-proxy chart doesn't deploy Redis metrics resources or Prometheus Operator resources.

PostgREST and nginx use Metrics Server for their default CPU and memory autoscalers. Istio exposes their traffic metrics for custom Prometheus-based KEDA triggers.

## Queries

```promql
sum by (table, status) (dump_tasks_total)
```

```promql
sum by (schema) (publish_tables_total{status="success"})
  / sum by (schema) (publish_tables_total)
```

---

[← Previous](helm_chart.md) · [Home](../README.md) · [Next →](backups.md)
