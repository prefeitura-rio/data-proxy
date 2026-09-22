# KEDA Scaling

The DBOS sync worker is a single Deployment scaled by one ScaledObject. The trigger counts DBOS queued and running workflows in the DBOS system database and scales the worker Deployment from zero (idle) to at least three replicas.

```yaml
triggers:
  - type: postgresql
    metadata:
      connectionFromEnv: AIRFLOW_CONN_AIRFLOW_DB
      query: "SELECT ceil(COUNT(*)::decimal / 16) FROM task_instance WHERE state='running' OR state='queued';"
      targetQueryValue: "1.1"
      activationTargetQueryValue: "5"
```

`connectionFromEnv` reads `AIRFLOW_CONN_AIRFLOW_DB`, which points at the DBOS system database. The query returns one unit of work per 16 queued or running workflows. `activationTargetQueryValue` activates from zero; `targetQueryValue` adds one replica per unit.

## Worker

| Value                            | Helm default | Meaning                                      |
| -------------------------------- | ------------ | -------------------------------------------- |
| `keda.idleReplicaCount`          | `1`          | Replicas when the DBOS queue is empty.       |
| `keda.minReplicaCount`           | `3`          | Minimum replicas once work activates.        |
| `keda.maxReplicaCount`           | `15`         | Maximum concurrent sync worker pods.         |
| `keda.pollingInterval`           | `30`         | Seconds between KEDA metric checks.          |
| `keda.cooldownPeriod`            | `60`         | Seconds before scaling down to idle.         |
| `keda.targetQueryValue`          | `"1.1"`      | Target value for the query result.           |
| `keda.activationTargetQueryValue`| `"5"`        | Value above which KEDA activates from zero.  |
| `dumpQueueWorkerConcurrency`     | `4`          | Per-process DBOS dump queue concurrency.     |
| `publishQueueWorkerConcurrency`  | `4`          | Per-process DBOS publish queue concurrency.  |
| `dumperStepMaxAttempts`          | `3`          | DBOS step retry attempts for one dump task.  |
| `dumper.batchMegaBytes`          | `600`        | Uncompressed batch target in MiB.            |
| `dumper.batchMaxPartitions`      | `256`        | Maximum partitions in one batch.             |

Set replica count from BigQuery quota and extraction capacity.

## Configuration

```yaml
sync:
  schedule: "0 2 * * *"
  worker:
    dumperStepMaxAttempts: 3
    dumpQueueWorkerConcurrency: 4
    publishQueueWorkerConcurrency: 4
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

The sync schedule is a DBOS scheduled workflow. DBOS deduplicates the schedule across worker replicas, so no separate CronJob is required. Keep one idle worker. The scheduled coordinator must reach a worker even when the queue has only one item.

---

[← Previous](fallback.md) · [Home](../README.md) · [Next →](database.md)
