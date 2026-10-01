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

## Pipeline errors

Workers publish structured error events to the bounded Redis Stream `dp:errors`. The stream is capped at approximately 10,000 entries. Use it to investigate failures without treating it as a durable audit log.

Events include the worker, error type, message, schema or table context when available, and the run identifier. The stream is written for diagnostics; normal sync state stays in the application state tables and Redis keys.

```bash
redis-cli XREAD COUNT 20 STREAMS dp:errors 0
```

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

| Source                                   | p95 limit |
| ---------------------------------------- | --------- |
| Cache                                    | 50 ms     |
| DuckLake                                 | 300 ms    |
| BigQuery (`bigquery`, `ducklake+bigquery`) | 6000 ms   |
| DuckLake heavy queries                   | 1000 ms   |
| DuckLake selective lookups               | 1000 ms   |
| DuckLake pinned snapshots                | 1000 ms   |

The smoke profile runs one VU for 40 seconds. Load and stress use open-model arrival rates, so each stage offers a fixed number of iterations per second even when responses slow down. Load ramps to 15 iterations/s and holds for 5 minutes. Stress steps through 10, 20, 30, 40, 50, and 60 iterations/s, with 1-minute holds (2 minutes at 60). Stress also samples the PostgREST and proxy replica counts every 5 seconds and fails if neither scales above its starting count. The high stress stages may reach latency limits; their thresholds are relaxed to measure overload while retaining checks for request failures, dropped iterations, and scale-out.

Each iteration of the load, stress, and smoke profiles sends one of three kinds of request:

- 20% bottleneck cases: half heavy queries (group-bys, sorted scans, jsonb filters), a quarter selective lookups by id, and a quarter reads at a pinned snapshot.
- 25% BigQuery pairs: one request to BigQuery and a repeat that must come from the cache.
- 55% normal routes.

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
