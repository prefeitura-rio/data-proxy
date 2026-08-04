"""Redis stream names, key patterns, and consumer group names."""

SYNC_TASKS_STREAM = "dp:sync:tasks"
SYNC_FINALIZE_STREAM = "dp:sync:finalize"
SYNC_JOB_KEY = "dp:sync:job:{sync_id}"
WORKERS_GROUP = "workers"
FINALIZERS_GROUP = "finalizers"
