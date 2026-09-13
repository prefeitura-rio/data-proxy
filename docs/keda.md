# KEDA Scaling

Data Proxy uses KEDA to scale the three pipeline workers. The Dumper is a ScaledObject, which scales a Deployment. The Seeder and the Publisher are ScaledJobs, which create one Job for each message. All three scale to zero between sync runs.

| Worker    | Kind        | Unit of work                      |
| --------- | ----------- | --------------------------------- |
| Dumper    | ScaledObject | One BigQuery extraction task, many tasks in parallel |
| Seeder    | ScaledJob   | One seed task for a run           |
| Publisher | ScaledJob   | One schema plan                   |

A ScaledJob creates a Job for each message. The Job pod processes its message, acknowledges it, and exits. Kubernetes does not restart the container, `backoffLimit` is 0 so the Job does not retry, and the Job reaches `Complete`. KEDA removes completed Jobs because `successfulJobsHistoryLimit` is 0 and keeps one failed Job because `failedJobsHistoryLimit` is 1.

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

| Parameter                  | Default | Description                                                                                                    |
| -------------------------- | ------- | -------------------------------------------------------------------------------------------------------------- |
| `maxReplicaCount`          | 1       | Only one seed Job runs at a time, because a run has one seed task.                                              |
| `successfulJobsHistoryLimit` | 0     | Number of completed seed Jobs that KEDA keeps.                                                                 |
| `failedJobsHistoryLimit`   | 1       | Number of failed seed Jobs that KEDA keeps for inspection.                                                      |
| `visibilityTimeoutMs`      | 900000  | Time a seed task must stay pending before a new seeder Job can reclaim it.                                      |

Triggers:

- `lagCount` on the `dp:prepare` Redis stream. Counts unread messages.
- `pendingEntriesCount` on the `dp:prepare` Redis stream. Counts messages that a seeder received but did not acknowledge. This trigger is the recovery path for a seed task whose Job died, so keep it.

### Publisher

| Parameter                  | Default           | Description                                                                                                                           |
| -------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `maxReplicaCount`          | Number of schemas | One publisher Job for each configured schema. Set a number to cap the Jobs.                                                            |
| `successfulJobsHistoryLimit` | 0               | Number of completed publisher Jobs that KEDA keeps.                                                                                    |
| `failedJobsHistoryLimit`   | 1                 | Number of failed publisher Jobs that KEDA keeps for inspection.                                                                        |
| `visibilityTimeoutMs`      | 7200000           | Time a publish task must stay pending before a new publisher Job can reclaim it. Set a value longer than the longest table publication. |

Triggers:

- `lagCount` on the `dp:publish` Redis stream. Counts unread messages.
- `pendingEntriesCount` on the `dp:publish` Redis stream. Counts messages that a publisher received but did not acknowledge. This trigger is the recovery path for a publish task whose Job died, so keep it.

Each publisher Job claims one publish task. The Seeder writes one publish task for each schema in the plan, so KEDA starts one Job for each schema. A Job acknowledges its message and completes after it publishes its schema. The Job that finishes the last schema reloads PostgREST, cleans the run state, and empties the bucket.

A message whose Job died stays pending until `visibilityTimeoutMs` expires. The pending trigger starts a Job for it, and that Job reclaims the message after the timeout. That Job idles until the timeout expires, so do not set `activeDeadlineSeconds` below the visibility timeout.

## Tuning

### Dumper maxReplicaCount

Set `maxReplicaCount` based on the number of extraction tasks and the BigQuery query quota. Each dumper pod runs one BigQuery `COPY` query. More pods extract data in parallel, but each query consumes BigQuery slots. Start with 15 and adjust based on BigQuery quota usage and extraction latency.

### cooldownPeriod

The `cooldownPeriod` controls how fast the Dumper Deployment scales down after work completes. The default of 60 seconds means KEDA waits one minute after the last trigger before scaling to zero. A shorter value reduces cost between sync runs. A longer value keeps pods warm for the next run. A ScaledJob has no cooldown period.

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
    successfulJobsHistoryLimit: 0
    failedJobsHistoryLimit: 1
  visibilityTimeoutMs: 900000

publisher:
  keda:
    maxReplicaCount: 2
    successfulJobsHistoryLimit: 0
    failedJobsHistoryLimit: 1
  visibilityTimeoutMs: 7200000
```
