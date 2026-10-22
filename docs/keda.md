# KEDA Scaling

KEDA scales the DBOS sync Deployment from the DBOS system database. The sync workers share the `data-proxy-duckdb` PVC and use schema-specific DBOS queues with global concurrency one.

```yaml
triggers:
  - type: postgresql
    metadata:
      connectionFromEnv: DBOS_SYSTEM_DATABASE_URL
      query: "SELECT ceil(COUNT(*)::decimal / 16) FROM dbos.workflow_status WHERE application_name = 'data-proxy-sync' AND status IN ('PENDING', 'ENQUEUED', 'DELAYED', 'RUNNING');
      targetQueryValue: "1.1"
      activationTargetQueryValue: "5"
```

`connectionFromEnv` reads `DBOS_SYSTEM_DATABASE_URL`. The query counts pending, queued, delayed, and running DBOS workflows for the sync application.

## Orchestrator and dump workers

| Value | Helm default | Meaning |
| --- | --- | --- |
| `autoscaling.minReplicaCount` | `3` | Minimum active sync replicas. |
| `autoscaling.maxReplicaCount` | `15` | Maximum sync replicas. |
| `autoscaling.pollingInterval` | `30` | Seconds between KEDA checks. |
| `autoscaling.cooldownPeriod` | `60` | Seconds before scale-down. |
| `autoscaling.targetQueryValue` | `"1.1"` | Target query value. |
| `autoscaling.activationTargetQueryValue` | `"5"` | Activation threshold. |
| `dumpQueueWorkerConcurrency` | `4` | Dump concurrency per sync process. |
| `ducklake.secrets.storage` | see values | Shared volume of the DuckDB secrets. |
| `dumperStepMaxAttempts` | `3` | Dump workflow retry attempts. |

## Catalogs

The DBOS sync workers write the catalogs in the `ducklake` database, through the primary. PostgreSQL replication copies them to every standby.

Publishing remains parallel across schemas because each schema has a separate metadata schema in the catalog database. Publishing remains sequential within one schema because its DBOS queue has global concurrency one.

## Configuration

```yaml
sync:
  schedule: "0 2 * * *"
  dumpStepMaxAttempts: 3
  dumpQueueWorkerConcurrency: 4
  # DBOS workers use the shared data-proxy-duckdb PVC.
  autoscaling:
    minReplicaCount: 3
    maxReplicaCount: 15
    pollingInterval: 30
    cooldownPeriod: 60
    targetQueryValue: "1.1"
    activationTargetQueryValue: "5"
```

DBOS deduplicates the scheduled workflow across orchestrator replicas. The sync Deployment never scales below `minReplicaCount`, so a new schedule always reaches a worker. The chart doesn't set `idleReplicaCount`, because KEDA supports only `0` for it and any other value makes KEDA and the HPA fight.

---

[← Previous](proxy.md) · [Home](../README.md) · [Next →](database.md)
