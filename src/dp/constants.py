"""Redis stream names, key patterns, and consumer group names."""

FINALIZERS_GROUP = "finalizers"
SYNC_FINALIZE_STREAM = "dp:sync:finalize"
SYNC_JOB_KEY = "dp:sync:job:{sync_id}"
SYNC_SHUTDOWN_CHANNEL = "dp:sync:shutdown"
SYNC_TASKS_STREAM = "dp:sync:tasks"
WORKERS_GROUP = "workers"
