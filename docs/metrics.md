# Metrics

Data Proxy pushes Prometheus metrics to a Pushgateway endpoint after each worker invocation. Set `PUSHGATEWAY_URL` to the Pushgateway address. The push is fire-and-forget: connection or HTTP errors are logged at debug level and swallowed.

## Available metrics

| Metric                           | Type      | Labels             | Description                                                                                                |
| -------------------------------- | --------- | ------------------ | ---------------------------------------------------------------------------------------------------------- |
| `dump_tasks_total`               | Counter   | `table`, `status`  | Total dump tasks processed. `status` is `success` or `failure`.                                            |
| `dump_task_duration_seconds`     | Histogram | `table`            | Dump task duration in seconds.                                                                             |
| `publish_tables_total`           | Counter   | `schema`, `status` | Total tables published. `status` is `success` or `failure`.                                                |
| `publish_table_duration_seconds` | Histogram | `table`            | Table publication duration in seconds.                                                                     |
| `seed_runs_total`                | Counter   | `status`           | Total seed runs processed. `status` is `success`, `recovered`, `no_changes`, or `active_run_conflict`.     |
| `producer_runs_total`            | Counter   | `status`           | Total producer runs processed. `status` is `success`, `recovered`, `no_changes`, or `active_run_conflict`. |

## Push pattern

Each worker decorator (`@tracker("dumper")`, `@tracker("publisher")`, etc.) pushes all registered metrics to the Pushgateway after the worker function completes. The Pushgateway holds metrics between scrapes. Prometheus scrapes the Pushgateway on its configured interval.

The push uses the worker name as the Prometheus job label. This separates metrics by worker type in the Pushgateway.

## Scraping

Configure Prometheus to scrape the Pushgateway:

```yaml
scrape_configs:
  - job_name: pushgateway
    static_configs:
      - targets: ["pushgateway.data-proxy.svc.cluster.local:9091"]
```

## Example queries

Total dump tasks by table and status:

```promql
sum by (table, status) (dump_tasks_total)
```

Average dump duration by table:

```promql
rate(dump_task_duration_seconds_sum[5m]) / rate(dump_task_duration_seconds_count[5m])
```

Publication success rate:

```promql
sum by (schema) (publish_tables_total{status="success"}) / sum by (schema) (publish_tables_total)
```

Producer run outcomes:

```promql
sum by (status) (producer_runs_total)
```
