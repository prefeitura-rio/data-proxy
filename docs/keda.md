# KEDA Scaling

KEDA scales the DBOS sync Deployment from the DBOS system database. The sync workers share the writer catalog PVC and use schema-specific DBOS queues with global concurrency one.

```yaml
triggers:
  - type: postgresql
    metadata:
      connectionFromEnv: AIRFLOW_CONN_AIRFLOW_DB
      query: "SELECT ceil(COUNT(*)::decimal / 16) FROM task_instance WHERE state='running' OR state='queued';"
      targetQueryValue: "1.1"
      activationTargetQueryValue: "5"
```

`connectionFromEnv` reads `AIRFLOW_CONN_AIRFLOW_DB`, which points to the DBOS system database. The query scales the DBOS sync workers. The separate `data-proxy-litestream` Deployment is fixed at one replica.

## Orchestrator and dump workers

| Value | Helm default | Meaning |
| --- | --- | --- |
| `keda.idleReplicaCount` | `1` | Replicas when the DBOS queue is empty. |
| `keda.minReplicaCount` | `3` | Minimum active sync replicas. |
| `keda.maxReplicaCount` | `15` | Maximum sync replicas. |
| `keda.pollingInterval` | `30` | Seconds between KEDA checks. |
| `keda.cooldownPeriod` | `60` | Seconds before scale-down. |
| `keda.targetQueryValue` | `"1.1"` | Target query value. |
| `keda.activationTargetQueryValue` | `"5"` | Activation threshold. |
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
  worker:
    dumperStepMaxAttempts: 3
    dumpQueueWorkerConcurrency: 4
    # DBOS workers use the shared writer catalog PVC.
    keda:
      idleReplicaCount: 1
      minReplicaCount: 3
      maxReplicaCount: 15
      pollingInterval: 30
      cooldownPeriod: 60
      targetQueryValue: "1.1"
      activationTargetQueryValue: "5"
  dumper:
    batchMegaBytes: 600
    batchMaxPartitions: 256
```

DBOS deduplicates the scheduled workflow across orchestrator replicas. Keep one idle orchestrator so a new schedule reaches a worker even when the queue is empty.

---

[← Previous](fallback.md) · [Home](../README.md) · [Next →](database.md)
