use std/log
use ./lib.nu [check-result execute-sql fail live-executor-ids parse-json quote-literal]

# The UIDs of the sync pods that are live executors of DBOS.
def current-executor-ids []: nothing -> list<string> {
    kubectl get pods --namespace $env.KUBERNETES_NAMESPACE --selector app.kubernetes.io/component=sync --output json
    | complete
    | check-result 'Listing of the sync pods'
    | parse-json
    | live-executor-ids
}

# Run one orphaned-workflow recovery scan.
def recover-orphaned-workflows []: nothing -> nothing {
    let executor_ids = current-executor-ids
    log info $'Workflow recovery scan started: live_executors=($executor_ids | length) executor_ids=($executor_ids | to json) grace=($env.DBOS_RECOVERY_GRACE_SECONDS)sec'

    execute-sql postgres/call_recover_orphaned_workflows.sql {
        schema: $env.DBOS_APP_SCHEMA
        live_executor_ids: (quote-literal ($executor_ids | to json))
        grace_seconds: $env.DBOS_RECOVERY_GRACE_SECONDS
    }

    log info $'Workflow recovery scan completed: live_executors=($executor_ids | length)'
}

# Look for orphaned workflows on a timer, and stop with an error when a scan fails.
def main []: nothing -> nothing {
    let interval = $'($env.DBOS_RECOVERY_INTERVAL_SECONDS)sec' | into duration

    log info $'Workflow recovery started: interval=($interval) grace=($env.DBOS_RECOVERY_GRACE_SECONDS)sec'

    loop {
        try {
            recover-orphaned-workflows
        } catch {|err|
            fail $'Workflow recovery failed: exiting=true error=($err.msg)'
        }
        log info $'Workflow recovery is waiting: sleep=($interval)'
        sleep $interval
    }
}
