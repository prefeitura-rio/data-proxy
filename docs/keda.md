# KEDA Scaling

KEDA scales the DBOS sync Deployment from the DBOS system database. The sync workers share the writer catalog PVC and use schema-specific DBOS queues with global concurrency one.

```yaml
triggers:
  - type: postgresql
    metadata:
      connectionFromEnv: DBOS_SYSTEM_DATABASE_URL
      query: "SELECT ceil(COUNT(*)::decimal / 16) FROM dbos.workflow_status WHERE application_name = 'data-proxy-sync' AND status IN ('PENDING', 'ENQUEUED', 'DELAYED', 'RUNNING');
      targetQueryValue: "1.1"
      activationTargetQueryValue: "5"
```

`connectionFromEnv` reads `DBOS_SYSTEM_DATABASE_URL`. The query counts pending, queued, delayed, and running DBOS workflows for the sync application. The separate `data-proxy-litestream` Deployment is fixed at one replica.

## Orchestrator and dump workers

| Value | Helm default | Meaning |
| --- | --- | --- |
| `autoscaling.idleReplicaCount` | `1` | Replicas when the DBOS queue is empty. |
| `autoscaling.minReplicaCount` | `3` | Minimum active sync replicas. |
| `autoscaling.maxReplicaCount` | `15` | Maximum sync replicas. |
| `autoscaling.pollingInterval` | `30` | Seconds between KEDA checks. |
| `autoscaling.cooldownPeriod` | `60` | Seconds before scale-down. |
| `autoscaling.targetQueryValue` | `"1.1"` | Target query value. |
| `autoscaling.activationTargetQueryValue` | `"5"` | Activation threshold. |
| `dumpQueueWorkerConcurrency` | `4` | Dump concurrency per sync process. |
| `ducklake.catalogStorage` | see values | Shared writer and reader PVC configuration. |
| `dumperStepMaxAttempts` | `3` | Dump workflow retry attempts. |
| `dumper.batchMegaBytes` | `600` | Uncompressed batch target in MiB. |
| `dumper.batchMaxPartitions` | `256` | Maximum partitions in one batch. |

## Catalog replication

The DBOS sync workers write the writer PVC. The single `data-proxy-litestream` Deployment replicates all writer catalogs and restores all reader catalogs. A restart restores the latest reader catalogs before PostgreSQL reads them.

Publishing remains parallel across schemas because each schema has a separate SQLite catalog. Publishing remains sequential within one schema because its DBOS queue has global concurrency one.

## Configuration

```yaml
sync:
  schedule: "0 2 * * *"
  dumpStepMaxAttempts: 3
  dumpQueueWorkerConcurrency: 4
  # DBOS workers use the shared writer catalog PVC.
  autoscaling:
    idleReplicaCount: 1
    minReplicaCount: 3
    maxReplicaCount: 15
    pollingInterval: 30
    cooldownPeriod: 60
    targetQueryValue: "1.1"
    activationTargetQueryValue: "5"
```

DBOS deduplicates the scheduled workflow across orchestrator replicas. Keep one idle orchestrator so a new schedule reaches a worker even when the queue is empty.

---

[← Previous](proxy.md) · [Home](../README.md) · [Next →](database.md)
