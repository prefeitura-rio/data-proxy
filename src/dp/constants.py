"""Redis stream names, key patterns, and consumer group names."""

FINALIZERS_GROUP = "finalizers"
SYNC_FINALIZE_STREAM = "dp:sync:finalize"
SYNC_JOB_KEY = "dp:sync:job:{sync_id}"
SYNC_JOB_TTL_SECONDS = 3_600
SYNC_PLAN_KEY = "dp:sync:plan:{sync_id}"
SYNC_PLAN_TTL_SECONDS = 18_000
SYNC_SHUTDOWN_CHANNEL = "dp:sync:shutdown"
SYNC_STATE_KEY = "dp:sync:state:{bq_table}"
SYNC_TASKS_STREAM = "dp:sync:tasks"
WORKERS_GROUP = "workers"
