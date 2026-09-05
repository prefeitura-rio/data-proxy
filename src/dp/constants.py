"""Redis streams and state keys for the synchronization pipeline."""

DUMPERS_GROUP = "dumpers"
SEEDERS_GROUP = "seeders"
PUBLISHERS_GROUP = "publishers"
DUMP_STREAM = "dp:extract"
SEED_STREAM = "dp:prepare"
PUBLISH_STREAM = "dp:publish"
ACTIVE_KEY = "dp:active"
PLANS_KEY = "dp:plans:{run_id}"
REMAINING_KEY = "dp:remaining:{run_id}"
RESULTS_KEY = "dp:results:{run_id}"
STATE_KEY = "dp:state:{table}"
SYNC_RUN_TTL_SECONDS = 7_200
SYNC_TRANSACTION_RETRIES = 8
BIGQUERY_TABLE_REFERENCE_PATTERN = (
    r"^(?P<project>[A-Za-z0-9_-]+)"
    r"\.(?P<dataset>[A-Za-z0-9_]+)"
    r"\.(?P<table>[A-Za-z0-9_$-]+)$"
)
