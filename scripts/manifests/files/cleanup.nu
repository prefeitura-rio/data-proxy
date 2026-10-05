use std/log

const NAMESPACE = 'data-proxy'
const TEST_SCHEMA = 'test'
const DB_PASSWORD = 'test-pg-pass'

# Clear E2E jobs, cache, test tables, state, and pending workflows.
def main []: nothing -> nothing {
    let stale_jobs = kubectl -n $NAMESPACE get jobs -o name
    | lines
    | where $it =~ 'data-proxy-(e2e|sync-k6|workflow-k6)-'

    if ($stale_jobs | is-not-empty) {
        kubectl -n $NAMESPACE delete ...$stale_jobs --ignore-not-found
    }

    let jsonpath = 'jsonpath={.items[0].metadata.name}'
    let valkey = kubectl -n $NAMESPACE get pod -l app.kubernetes.io/name=valkey -o $jsonpath
    | str trim

    if ($valkey | is-empty) {
        error make 'Valkey pod is unavailable during test cleanup'
    }

    kubectl -n $NAMESPACE exec $valkey -- redis-cli -n 1 FLUSHDB

    let clusters = try {
        kubectl -n $NAMESPACE get clusters -o json
        | from json
        | get items
        | get metadata.name
    } catch {|err| error make $'Could not read CNPG clusters during test cleanup: ($err.msg)' }

    for cluster in $clusters {
        let primary = kubectl -n $NAMESPACE get pod -l $'cnpg.io/cluster=($cluster)' -l cnpg.io/instanceRole=primary -o $jsonpath
        | str trim

        if ($primary | is-empty) {
            error make $'CNPG primary is unavailable during test cleanup: ($cluster)'
        }

        let dsn = $'postgresql://admin:($DB_PASSWORD)@($cluster)-rw:5432/data-proxy'
        if $cluster == 'data-proxy-dbos' {
            kubectl -n $NAMESPACE exec $primary -- psql $dsn -v ON_ERROR_STOP=1 -c 'DELETE FROM data_proxy.state; DELETE FROM data_proxy.errors;'
            continue
        }

        let schema = if $cluster == 'data-proxy' {
            $TEST_SCHEMA
        } else {
            $cluster | str replace 'data-proxy-' ''
        }

        let context = '/tmp/cleanup.json'

        let query = try {
            {schema: $schema}
            | to json
            | save --force $context
            minijinja-cli --strict --autoescape none --format json /scripts/cleanup.sql $context
        } catch {|err| error make $'Could not render cleanup SQL: ($err.msg)' }

        kubectl -n $NAMESPACE exec $primary -- psql $dsn -v ON_ERROR_STOP=1 -c $query

        if $cluster == 'data-proxy' {
            kubectl -n $NAMESPACE exec $primary -- psql $dsn -v ON_ERROR_STOP=1 -c "DELETE FROM data_proxy.state; DELETE FROM data_proxy.errors; UPDATE dbos.workflow_status SET status = 'CANCELLED', error = 'Cancelled before test run' WHERE application_name = 'data-proxy-sync' AND status IN ('PENDING', 'ENQUEUED', 'DELAYED');"
        }
    }

    log info 'Test cleanup completed'
}
