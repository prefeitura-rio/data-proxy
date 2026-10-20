use std/log

const NAMESPACE = 'data-proxy'
const TEST_SCHEMA = 'test'
const NEW_SCHEMA = 'e2e_new'
const DB_PASSWORD = 'test-pg-pass'
const JSONPATH = 'jsonpath={.items[0].metadata.name}'

# Return the first pod name for a label selector, or fail with a description when there is none.
def first-pod [selector: string, description: string]: nothing -> string {
    let name = kubectl -n $NAMESPACE get pod -l $selector -o $JSONPATH | str trim

    if ($name | is-empty) {
        error make --unspanned {msg: $'($description) is unavailable during test cleanup'}
    }

    $name
}

# Render the cleanup SQL for the data database.
def cleanup-sql []: nothing -> string {
    let context = mktemp --tmpdir --suffix .json

    {schema: $TEST_SCHEMA, new_schema: $NEW_SCHEMA} | to json | save --force $context
    let sql = minijinja-cli --strict --autoescape none --format json /scripts/cleanup.sql $context
    rm --force $context

    $sql
}

# Clear the E2E helper Jobs, cache, test tables, state, and pending workflows.
# The runner Jobs of the TestRun stay, because the suite runs this cleanup in its own teardown.
def main []: nothing -> nothing {
    let stale_jobs = kubectl -n $NAMESPACE get jobs -o name
    | lines
    | where $it =~ 'data-proxy-(sync-k6|workflow-k6)-'

    if ($stale_jobs | is-not-empty) {
        kubectl -n $NAMESPACE delete ...$stale_jobs --ignore-not-found
    }

    kubectl -n $NAMESPACE exec (first-pod app.kubernetes.io/name=valkey 'Valkey pod') -- redis-cli -n 1 FLUSHDB

    let query = cleanup-sql
    let clusters = kubectl -n $NAMESPACE get clusters -o json | from json | get items | get metadata.name

    for cluster in $clusters {
        let primary = first-pod $'cnpg.io/cluster=($cluster),cnpg.io/instanceRole=primary' $'CNPG primary of ($cluster)'
        let server = $'postgresql://ops:($DB_PASSWORD)@($cluster)-rw:5432'

        kubectl -n $NAMESPACE exec $primary -- psql $'($server)/data-proxy' -v ON_ERROR_STOP=1 -c $query
        kubectl -n $NAMESPACE exec $primary -- psql $'($server)/ducklake' -v ON_ERROR_STOP=1 -c $'DROP SCHEMA IF EXISTS ($NEW_SCHEMA) CASCADE'
    }

    log info 'Test cleanup completed'
}
