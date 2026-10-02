# Metrics

Workers push the current metric registry to Pushgateway after running. Push failures are logged and ignored.

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

The proxy writes one JSON request log per request.

| Field     | Meaning                                      |
| --------- | -------------------------------------------- |
| `source`  | `cache`, the `X-Source` value from PostgREST (`ducklake`, `bigquery`, `ducklake+bigquery`), `upstream` when PostgREST sent none, or `none` when it was unreachable. |
| `status`  | HTTP status.                                 |
| `wait_ms` | Request duration in milliseconds.            |
| `bytes`   | Response size.                               |

Use API response headers for client troubleshooting. Use logs for request analysis. See [Using the API](using.md#response-source-and-cache).

## Load profile limits

| Source                                     | Bound                         | Gates a run        |
| ------------------------------------------ | ----------------------------- | ------------------ |
| Cache                                      | p95 50 ms, p99 100 ms         | yes                |
| DuckLake bounded reads                     | p95 300 ms, p99 600 ms        | yes                |
| BigQuery (`bigquery`, `ducklake+bigquery`) | p95 6000 ms                   | no, client risk    |
| DuckLake heavy queries                     | p95 2000 ms, p99 5000 ms      | no, client risk    |
| DuckLake selective lookups                 | p95 2000 ms, p99 5000 ms      | no, client risk    |
| DuckLake pinned snapshots                  | p95 2000 ms, p99 5000 ms      | no, client risk    |

Every profile derives its rate from one local peak, `K6_PEAK_RATE`, which defaults to 100 HTTP requests per second. The smoke profile runs one VU for 40 seconds. The other profiles use open-model arrival rates, so each stage offers a fixed iteration rate even when responses slow down: `load` holds the peak for 25 minutes, `spike` bursts to three times peak and drains, `stress` steps to two times peak, and `soak` holds 65% of peak for an hour. Stress and soak sample the PostgREST and proxy replica counts every 5 seconds and fail if neither scales above its starting count. Staging capacity runs override `K6_PEAK_RATE` with a higher peak from a generator outside the cluster host.

Because the arrival-rate executor counts iterations and a BigQuery iteration makes two requests, a profile converts the target request rate into an iteration rate internally. The high stress and spike stages may reach latency limits. Their thresholds widen the error budget to measure overload, while the stack-owned paths still require every check, no dropped iterations, and the bounded-read and cache bounds.

The default traffic mix models production: 5% bottleneck cases (half heavy queries, a quarter selective lookups, a quarter pinned snapshots), 5% BigQuery pairs (one request to BigQuery and a repeat that must come from the cache), and 90% normal routes. Set `K6_BOTTLENECK_SHARE` and `K6_BIGQUERY_SHARE` to model a different mix.

## Scraping

CNPG exposes metrics on port 9187. SigNoz should scrape the CNPG pod metrics endpoint through its OpenTelemetry Collector. Select CNPG pods by their cluster labels and scrape the named `metrics` port.

The external Redis platform owns the Redis exporter and its metrics endpoint. The data-proxy chart doesn't deploy Redis metrics resources or Prometheus Operator resources.

PostgREST and nginx use Metrics Server for their default CPU and memory autoscalers. Istio exposes their traffic metrics for custom Prometheus-based KEDA triggers.

```yaml
scrape_configs:
  - job_name: pushgateway
    static_configs:
      - targets: ["<release>-pushgateway.<namespace>.svc.cluster.local:9091"]
```

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
