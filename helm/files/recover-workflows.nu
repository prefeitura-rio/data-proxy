use std/log
use ./lib.nu [execute-sql quote-pg fail]

def live-executor-ids []: nothing -> list<string> {
    let result = kubectl get pods --namespace $env.KUBERNETES_NAMESPACE --selector app.kubernetes.io/component=sync --output json | complete

    if $result.exit_code != 0 {
        fail $'Failed to list sync pods: ($result.stderr | str trim)' {
            command: live-executor-ids
            span: (metadata $result).span
        }
    }

    try {
        $result.stdout
        | from json
        | get items
        | where $it.status.phase == Running
        | where $it.metadata.deletionTimestamp? == null
        | get metadata.uid
    } catch {|err|
        fail $'Failed to read sync pod UIDs: ($err.msg)' {
            command: live-executor-ids
            span: (metadata $result.stdout).span
        }
    }
}

# Run one orphaned-workflow recovery scan.
def recover-orphaned-workflows []: nothing -> nothing {
    let executor_ids = live-executor-ids
    log info $'Workflow recovery scan started live_executors=($executor_ids | length)'

    execute-sql postgres/call_recover_orphaned_workflows.sql {
        schema: $env.DBOS_APP_SCHEMA
        live_executor_ids: (quote-pg ($executor_ids | to json) literal)
        grace_seconds: $env.DBOS_RECOVERY_GRACE_SECONDS
    }

    log info $'Workflow recovery procedure completed live_executors=($executor_ids | length)'
}

def main []: nothing -> nothing {
    let interval = $'($env.DBOS_RECOVERY_INTERVAL_SECONDS)sec' | into duration

    loop {
        try {
            recover-orphaned-workflows
        } catch {|err| log error $'Workflow recovery failed: ($err.msg)' }

        sleep $interval
    }
}
