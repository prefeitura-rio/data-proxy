# Metrics

Workers push the current metric registry to Pushgateway after execution. Push failures are logged and ignored.

## Worker metrics

| Metric                           | Labels             | Meaning                                                                   |
| -------------------------------- | ------------------ | ------------------------------------------------------------------------- |
| `dump_tasks_total`               | `table`, `status`  | Dump tasks. Status: `success` or `failure`.                               |
| `dump_task_duration_seconds`     | `table`            | Dump duration.                                                            |
| `publish_tables_total`           | `schema`, `status` | Published tables. Status: `success` or `failure`.                         |
| `publish_table_duration_seconds` | `table`            | Schema publication duration recorded for each published table.            |
| `seed_runs_total`                | `status`           | Seed runs. Current status: `success`.                                     |
| `producer_runs_total`            | `status`           | Producer runs. Status: `success`, `no_changes`, or `active_run_conflict`. |

## Pipeline errors

Workers publish structured failure events to the bounded Redis Stream `dp:errors`. The stream is capped at approximately 10,000 entries. Use it to investigate failures without treating it as a durable audit log.

Events include the worker, error type, message, schema or table context when available, and the run identifier. The stream is written for diagnostics; normal pipeline state remains in the application state tables and Redis keys.

```bash
redis-cli XREAD COUNT 20 STREAMS dp:errors 0
```

## Proxy logs

The fallback proxy writes one JSON request log per request.

| Field     | Meaning                                      |
| --------- | -------------------------------------------- |
| `source`  | `cache`, `postgrest`, `bigquery`, or `none`. |
| `status`  | HTTP status.                                 |
| `wait_ms` | Request duration in milliseconds.            |
| `bytes`   | Response size.                               |

Use API response headers for client troubleshooting. Use logs for request analysis. See [Using the API](using.md#response-source-and-cache).

## Load profile limits

| Source            | p95 limit |
| ----------------- | --------- |
| Cache             | 50 ms     |
| Local PostgREST   | 300 ms    |
| BigQuery fallback | 4000 ms   |

## Scraping

CNPG exposes metrics on port 9187. SigNoz should scrape the CNPG pod metrics endpoint through its OpenTelemetry Collector. Select CNPG pods by their cluster labels and scrape the named `metrics` port.

The external Redis platform owns the Redis exporter and its metrics endpoint. The data-proxy chart does not deploy Redis metrics resources or Prometheus Operator resources.

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
