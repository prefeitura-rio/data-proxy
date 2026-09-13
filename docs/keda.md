# KEDA Scaling

Dumper is a ScaledObject. Seeder and Publisher are ScaledJobs. All workers scale to zero when their streams are empty.

| Worker    | Work unit                          | Stream       |
| --------- | ---------------------------------- | ------------ |
| Dumper    | One full table or partition batch. | `dp:extract` |
| Seeder    | One run.                           | `dp:prepare` |
| Publisher | One schema plan.                   | `dp:publish` |

`lagCount` starts work for unread messages. `pendingEntriesCount` starts recovery work for pending messages after the visibility timeout.

## Dumper

| Value                  | Helm default | Meaning                           |
| ---------------------- | ------------ | --------------------------------- |
| `keda.maxReplicaCount` | `15`         | Maximum concurrent dump pods.     |
| `keda.cooldownPeriod`  | `60`         | Seconds before scale-to-zero.     |
| `visibilityTimeoutMs`  | `3600000`    | Pending-message reclaim timeout.  |
| `batchMegaBytes`       | `600`        | Uncompressed batch target in MiB. |
| `batchMaxPartitions`   | `256`        | Maximum partitions in one batch.  |

Set the timeout longer than normal extraction duration. Set replica count from BigQuery quota and extraction capacity.

## Seeder and Publisher

| Value              | Seeder      | Publisher                                    |
| ------------------ | ----------- | -------------------------------------------- |
| Maximum jobs       | `1`         | Number of configured schemas, unless capped. |
| Successful history | `0`         | `0`                                          |
| Failed history     | `1`         | `1`                                          |
| Visibility timeout | `900000` ms | `7200000` ms                                 |

A ScaledJob handles one message, acknowledges it, and exits. `backoffLimit` is `0`; recovery uses the pending stream trigger instead of Kubernetes retries.

## Configuration

```yaml
dumper:
  visibilityTimeoutMs: 3600000
  batchMegaBytes: 600
  batchMaxPartitions: 256
  keda:
    maxReplicaCount: 15
    cooldownPeriod: 60

seeder:
  visibilityTimeoutMs: 900000

publisher:
  visibilityTimeoutMs: 7200000
  keda:
    maxReplicaCount: 2
```

Do not set `activeDeadlineSeconds` below the matching visibility timeout.

---

[← Previous](fallback.md) · [Home](../README.md) · [Next →](database.md)
