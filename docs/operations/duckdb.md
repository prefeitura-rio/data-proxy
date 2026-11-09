# DuckDB Memory and Spill

These values tune the memory and the spill of the sync workers. The defaults are in [`helm/values.yaml`](../../helm/values.yaml). [KEDA Scaling](keda.md) describes the scale of the workers.

| Value | Default | Effect |
| --- | --- | --- |
| `sync.duckdb.memoryLimit` | `1GB` | The memory of each DuckDB connection of a sync worker. DuckDB spills to the disk beyond it. |
| `sync.duckdb.threads` | `2` | The threads of each DuckDB connection. |
| `sync.duckdb.tempDirectory` | `/tmp/duckdb` | The folder where DuckDB spills beyond `memoryLimit`. Each connection makes a folder of its own in it. |
| `sync.duckdb.tempVolume.enabled` | `false` | Mounts a volume at `tempDirectory`. When it's off, the spill uses the filesystem of the container, which is the disk of the node. |
| `sync.duckdb.tempVolume.type` | `ephemeral` | `ephemeral` makes a claim for each pod and deletes it with the pod. `emptyDir` uses the disk of the node. |
| `sync.duckdb.tempVolume.size` | `50Gi` | The size of the claim, or the size limit of the `emptyDir`. |
| `sync.duckdb.tempVolume.storageClassName` | empty | The StorageClass of the claim. Empty uses the default StorageClass. |

## Memory

A DuckDB connection takes 80% of the memory of the pod and every thread of the node. A pod has one connection for each dump task and one for each publish. Without a limit, the connections of one pod fill the pod and Kubernetes kills it (`OOMKilled`).

`sync.duckdb.memoryLimit` limits each connection. Each connection has its own spill folder, because two connections in one folder overwrite the files of each other and DuckDB crashes. Keep `memoryLimit` times (`sync.dumpQueueWorkerConcurrency` + 2) below `sync.resources.limits.memory`. The 2 stands for a publish and a maintenance run. Four connections of 1 GB (two dumps, a publish and a maintenance run) use 4 GB of the 6 GiB of a pod.

A row group is in memory while DuckDB writes it. The default of 122880 rows needs more than 3 GB for a table with wide JSON columns. The dump writes row groups of 10000 rows. The publish writes row groups of 122880 rows and stops a row group at 32 MB. The limit in bytes, not the rows, bounds the memory of wide tables. The two widest tables took 1.1 GB with it, and they ran out of memory at 1 GB without it. A narrow table keeps the large row groups, which DuckDB reads about twice as fast as groups of 10000 rows.

## Spill volume

Without a volume, the spill folder is on the filesystem of the container, which is the disk of the node. A large spill can fill that disk, and then Kubernetes evicts the pod.

Set `sync.duckdb.tempVolume.enabled` to give the folder a disk of its own. Use `type: ephemeral` with a fast `storageClassName` for a large spill: the chart makes a claim for each pod and deletes it with the pod. Use `type: emptyDir` to cap the use of the node disk with `size`.

A disk-backed volume belongs to root. The chart sets `fsGroup` on the sync pod to the group of the sync user, so the sync can write to the volume.

Don't mount a bucket through FUSE as the spill folder. Each connection writes and reads small blocks at random, and a bucket mount is slow for that access. Each connection also needs a folder of its own.

<!-- nav:start -->
---

[Previous: KEDA Scaling](./keda.md) | [Next: Metrics](./metrics.md) | [Index](./index.md) | [Home](../index.md)
<!-- nav:end -->
