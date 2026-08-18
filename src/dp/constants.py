"""Redis stream names, key patterns, and consumer group names."""

FINALIZERS_GROUP = "finalizers"
RECLAIM_MIN_IDLE_MS = 300_000
SYNC_FINALIZE_STREAM = "dp:sync:finalize"
SYNC_JOB_KEY = "dp:sync:job:{sync_id}"
SYNC_JOB_TTL_SECONDS = 3_600
SYNC_PARTITIONS_KEY = "dp:sync:partitions:{bq_table}"
SYNC_PLAN_KEY = "dp:sync:plan:{sync_id}"
SYNC_PLAN_TTL_SECONDS = 18_000
SYNC_SHUTDOWN_CHANNEL = "dp:sync:shutdown"
SYNC_STATE_KEY = "dp:sync:state:{bq_table}"
SYNC_TASKS_STREAM = "dp:sync:tasks"
WORKERS_GROUP = "workers"

BIGQUERY_TABLE_REFERENCE_PATTERN = (
    r"^(?P<project>[A-Za-z0-9_-]+)"
    r"\.(?P<dataset>[A-Za-z0-9_]+)"
    r"\.(?P<table>[A-Za-z0-9_$-]+)$"
)
