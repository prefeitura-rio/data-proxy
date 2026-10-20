use std/log
use ./lib.nu [check-result execute-sql quote-literal]

# Return the UIDs of the sync pods that are running and not deleting, which are the live executors of DBOS.
def live-executor-ids []: nothing -> list<string> {
    kubectl get pods --namespace $env.KUBERNETES_NAMESPACE --selector app.kubernetes.io/component=sync --output json
    | complete
    | check-result 'Listing of the sync pods'
    | from json
    | get items
    | where $it.status.phase == Running
    | where $it.metadata.deletionTimestamp? == null
    | get metadata.uid
}

# Run one orphaned-workflow recovery scan.
def recover-orphaned-workflows []: nothing -> nothing {
    let executor_ids = live-executor-ids
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
            log error $'Workflow recovery failed: exiting=true error=($err.msg)'
            error make $err.raw
        }
        log info $'Workflow recovery is waiting: sleep=($interval)'
        sleep $interval
    }
}
