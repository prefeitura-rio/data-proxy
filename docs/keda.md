# KEDA Scaling

Data Proxy uses KEDA ScaledObject resources to scale the Dumper, Seeder, and Publisher. A ScaledObject scales a Deployment based on trigger metrics. All three scale to zero between sync runs.

## Resources

### Dumper

| Parameter             | Default | Description                                                                                                              |
| --------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------ |
| `maxReplicaCount`     | 15      | Maximum number of dumper pods. Each pod processes one extraction task.                                                   |
| `cooldownPeriod`      | 60      | Seconds after the last active trigger before KEDA scales to zero.                                                        |
| `visibilityTimeoutMs` | 900000  | Time a dump task must stay pending before a new dumper can reclaim it. Set a value longer than the normal dump duration. |

Triggers:

- `lagCount` on the `dp:extract` Redis stream. Counts unread messages.
- `pendingEntriesCount` on the `dp:extract` Redis stream. Counts messages that a dumper received but did not acknowledge.

### Seeder

| Parameter             | Default | Description                                                            |
| --------------------- | ------- | ---------------------------------------------------------------------- |
| `maxReplicaCount`     | 1       | Only one seeder pod runs at a time.                                    |
| `cooldownPeriod`      | 60      | Seconds after the last active trigger before KEDA scales to zero.      |
| `visibilityTimeoutMs` | 900000  | Time a seed task must stay pending before a new seeder can reclaim it. |

Trigger:

- `lagCount` on the `dp:prepare` Redis stream.

### Publisher

| Parameter             | Default | Description                                                                                                                           |
| --------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `maxReplicaCount`     | 1       | Only one publisher pod runs at a time.                                                                                                |
| `cooldownPeriod`      | 60      | Seconds after the last active trigger before KEDA scales to zero.                                                                     |
| `visibilityTimeoutMs` | 900000  | Time a publish task must stay pending before a new publisher can reclaim it. Set a value longer than the normal publication duration. |

Trigger:

- `lagCount` on the `dp:publish` Redis stream.

## Tuning

### Dumper maxReplicaCount

Set `maxReplicaCount` based on the number of extraction tasks and the BigQuery query quota. Each dumper pod runs one BigQuery `COPY` query. More pods extract data in parallel, but each query consumes BigQuery slots. Start with 15 and adjust based on BigQuery quota usage and extraction latency.

### cooldownPeriod

The `cooldownPeriod` controls how fast pods scale down after work completes. The default of 60 seconds means KEDA waits one minute after the last trigger before scaling to zero. A shorter value reduces cost between sync runs. A longer value keeps pods warm for the next run.

### visibilityTimeoutMs

The visibility timeout prevents two pods from processing the same message. Set it longer than the expected task duration. If a task takes longer than the timeout, a second pod reclaims the message and runs the task again. For the publisher, set the timeout longer than the longest table publication.

## Configuration

Set these values in your Helm values file:

```yaml
dumper:
  keda:
    maxReplicaCount: 15
    cooldownPeriod: 60
  visibilityTimeoutMs: 900000

seeder:
  keda:
    cooldownPeriod: 60
  visibilityTimeoutMs: 900000

publisher:
  keda:
    cooldownPeriod: 60
  visibilityTimeoutMs: 900000
```
