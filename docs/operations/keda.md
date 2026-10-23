# KEDA Scaling

Kubernetes Event-driven Autoscaling (KEDA) scales the DBOS sync Deployment from the DBOS system database. The sync workers share the `data-proxy-duckdb` PersistentVolumeClaim (PVC). They publish through one `publish` queue that's partitioned by schema, so each schema runs one publish task at a time.

KEDA also scales PostgREST, the proxy and the CloudNativePG (CNPG) standbys. [Topology](topology.md) describes how.

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

These Helm values set the scale and the concurrency of the sync workers. The table gives the default of each value.

| Value | Helm default | Meaning |
| --- | --- | --- |
| `sync.autoscaling.minReplicaCount` | `3` | Minimum active sync replicas. |
| `sync.autoscaling.maxReplicaCount` | `15` | Maximum sync replicas. |
| `sync.autoscaling.pollingInterval` | `30` | Seconds between KEDA checks. |
| `sync.autoscaling.cooldownPeriod` | `60` | Seconds before scale-down. |
| `sync.autoscaling.targetQueryValue` | `"1.1"` | Target query value. |
| `sync.autoscaling.activationTargetQueryValue` | `"5"` | Activation threshold. |
| `sync.dumpQueueWorkerConcurrency` | `2` | Dump concurrency per sync process. |
| `sync.duckdb.memoryLimit` | `1GB` | Memory of each DuckDB connection. |
| `sync.duckdb.threads` | `2` | Threads of each DuckDB connection. |
| `ducklake.secrets.storage` | see values | Shared volume of the DuckDB secrets. |
| `sync.dumpStepMaxAttempts` | `3` | Dump workflow retry attempts. |

## Memory

A DuckDB connection takes 80% of the memory of the pod and every thread of the node. A pod has one connection for each dump task and one for each publish. Without a limit, the connections of one pod fill the pod and Kubernetes kills it (`OOMKilled`).

`sync.duckdb.memoryLimit` limits each connection and DuckDB spills to the disk beyond it. Each connection has its own spill folder, because two connections in one folder overwrite the files of each other and DuckDB crashes. Keep `memoryLimit` times (`dumpQueueWorkerConcurrency` + 2) below `sync.resources.limits.memory`. The 2 stands for a publish and a maintenance run.

A row group is in memory while DuckDB writes it. The default of 122880 rows needs more than 3 GB for a table with wide JSON columns. The dump writes row groups of 10000 rows. The publish also stops a row group at 32 MB. With the limit in bytes, the two widest tables took 1.1 GB. Without it, they ran out of memory at 1 GB.

A worker takes small batches: two dumps at a time. Four connections of 1 GB (two dumps, a publish and a maintenance run) use 4 GB of the 6 GiB of a pod. KEDA adds pods when more dumps wait.

The queue stores its limits when it starts for the first time, and a restart keeps them. A change of `sync.dumpQueueWorkerConcurrency` therefore changes only a new installation. To change an installed queue, update the row in the system database. The workers read the row again within seconds, so they need no restart.

```sql
UPDATE dbos.queues SET worker_concurrency = 2 WHERE name = 'dump';
```

## Catalogs

The DBOS sync workers write the catalogs in the `ducklake` database, through the primary. PostgreSQL replication copies them to every standby.

Publishing remains parallel across schemas because each schema has a separate metadata schema in the catalog database. Publishing remains sequential within one schema because its DBOS queue has global concurrency one.

## Configuration

This example shows the Helm values that set the schedule, the retries and the KEDA scaling of the sync workers.

```yaml
sync:
  schedule: "0 2 * * *"
  dumpStepMaxAttempts: 3
  dumpQueueWorkerConcurrency: 2
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

<!-- nav:start -->
---

[Previous: Environment Variables](environment.md) | [Next: Metrics](metrics.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
