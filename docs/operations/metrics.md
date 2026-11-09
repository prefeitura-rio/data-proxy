# Metrics

Sync workers send their metrics through OpenTelemetry when `OTLP_METRICS_ENDPOINT` is set. They use the OpenTelemetry Protocol (OTLP).

## Worker metrics

The sync workers export these Prometheus metrics. The table gives the labels and the meaning of each metric.

| Metric                 | Labels                      | Meaning                                                       |
| ---------------------- | --------------------------- | ------------------------------------------------------------- |
| `dump_tasks_total`     | `table`, `schema`, `status` | Dump tasks. Status: `success` or `error`.                     |
| `publish_tables_total` | `schema`, `status`          | Published tables. Status: `success` or `error`.               |
| `seed_runs_total`      | `schema`, `status`          | Seed runs. Current status: `success`.                         |
| `sync_runs_total`      | `status`                    | Sync runs. Status: `success` or `failure`.                    |

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

Use API response headers for client troubleshooting. Use logs for request analysis. See [Using the API](../using.md#response-source-and-cache).

## PostgREST metrics

PostgREST serves Prometheus metrics at `/metrics` on its admin port, 3001 by default. The pod has the `prometheus.io/*` scrape annotations. Set `postgrest.metrics.enabled` to `false` to turn the admin port off.

The port is open to the pods in the cluster, and `/schema_cache` on it shows the schema structure.

| Metric                          | Meaning                                                                               |
| ------------------------------- | ------------------------------------------------------------------------------------- |
| `pgrst_db_pool_waiting`         | Requests that wait for a database connection. A value above 0 means that the pool is full. |
| `pgrst_db_pool_available`       | Free connections in the pool.                                                         |
| `pgrst_db_pool_timeouts_total`  | Requests that gave up after `dbPoolAcquisitionTimeout` seconds. A rising value means that clients get errors. |

Alert when `pgrst_db_pool_waiting` stays above 0, and when the timeouts counter rises. Both point to a database that needs more capacity, or to a request mix that's too demanding.

## Load profile limits

The load test gates a run on latency bounds for the cache and for bounded DuckLake reads. It reports the heavy, selective, pinned and fallback routes. The header of `k6/load.js` lists the bounds, the traffic mix and the flags. [Load tests](../dev/tests.md#end-to-end-and-load-tests) shows the commands.

## Capacity diagnosis

Check these signals during a load run. Compare pool waits with DuckLake and configured-fallback latency in the same PostgREST instance.

```sql
SELECT state, wait_event_type, wait_event, count(*)
FROM pg_stat_activity
WHERE usename = 'serving'
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

One PostgREST and one PgDog serve both DuckLake and configured fallback reads. The proxy logs separate cache hits, DuckLake reads and configured fallback reads. A rising `duration_ms` p95 for DuckLake reads with low PostgreSQL execution time indicates pool wait or request queueing. A rising fallback trend indicates external-source latency. Keep the local k3d result as a comparison only: the load generator and services share one host.

## Scraping

CloudNativePG (CNPG) exposes metrics on port 9187. SigNoz should scrape the CNPG pod metrics endpoint through its OpenTelemetry Collector. Select CNPG pods by their cluster labels and scrape the named `metrics` port.

The external Redis platform runs the Redis exporter and its metrics endpoint. The data-proxy chart doesn't deploy Redis metrics resources or Prometheus Operator resources.

PostgREST and nginx use Metrics Server for their default CPU and memory autoscalers. Istio exposes their traffic metrics for custom Prometheus-based Kubernetes Event-driven Autoscaling (KEDA) triggers.

## Queries

These PromQL queries count the dump tasks and the published tables of the sync workers.

```promql
sum by (table, status) (dump_tasks_total)
```

```promql
sum by (schema) (publish_tables_total{status="success"})
  / sum by (schema) (publish_tables_total)
```

---

<!-- nav:start -->
---

[Previous: DuckDB Memory and Spill](./duckdb.md) | [Next: Backups](./backups.md) | [Index](./index.md) | [Home](../index.md)
<!-- nav:end -->
