use std/log

const NAMESPACE = 'data-proxy'
const TEST_SCHEMA = 'test'
const NEW_SCHEMA = 'e2e_new'
const DB_PASSWORD = 'test-pg-pass'

# Clear the E2E helper Jobs, cache, test tables, state, and pending workflows.
# The runner Jobs of the TestRun stay, because the suite runs this cleanup in its own teardown.
def main []: nothing -> nothing {
    let stale_jobs = kubectl -n $NAMESPACE get jobs -o name
    | lines
    | where $it =~ 'data-proxy-(sync-k6|workflow-k6)-'

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

        let dsn = $'postgresql://ops:($DB_PASSWORD)@($cluster)-rw:5432/data-proxy'
        let context = '/tmp/cleanup.json'

        let query = try {
            {schema: $TEST_SCHEMA}
            | to json
            | save --force $context
            minijinja-cli --strict --autoescape none --format json /scripts/cleanup.sql $context
        } catch {|err| error make $'Could not render cleanup SQL: ($err.msg)' }

        kubectl -n $NAMESPACE exec $primary -- psql $dsn -v ON_ERROR_STOP=1 -c $query

        let new_schema = "DO \$\$ BEGIN IF to_regclass('console.schemas') IS NOT NULL THEN DELETE FROM console.tables WHERE schema = 'SCHEMA_NAME'; DELETE FROM console.schemas WHERE name = 'SCHEMA_NAME'; END IF; END \$\$; DROP SCHEMA IF EXISTS SCHEMA_NAME CASCADE;"
        | str replace --all 'SCHEMA_NAME' $NEW_SCHEMA
        kubectl -n $NAMESPACE exec $primary -- psql $dsn -v ON_ERROR_STOP=1 -c $new_schema

        let ducklake_dsn = $dsn | str replace '/data-proxy' '/ducklake'
        kubectl -n $NAMESPACE exec $primary -- psql $ducklake_dsn -v ON_ERROR_STOP=1 -c $'DROP SCHEMA IF EXISTS ($NEW_SCHEMA) CASCADE'

        kubectl -n $NAMESPACE exec $primary -- psql $dsn -v ON_ERROR_STOP=1 -c "DO \$\$ BEGIN IF to_regclass('data_proxy.state') IS NOT NULL THEN DELETE FROM data_proxy.state; END IF; IF to_regclass('data_proxy.errors') IS NOT NULL THEN DELETE FROM data_proxy.errors; END IF; IF to_regclass('data_proxy.scope') IS NOT NULL THEN DELETE FROM data_proxy.scope; END IF; IF to_regclass('dbos.workflow_status') IS NOT NULL THEN UPDATE dbos.workflow_status SET status = 'CANCELLED', error = 'Cancelled before test run', deduplication_id = NULL WHERE application_name = 'data-proxy-sync' AND status IN ('PENDING', 'ENQUEUED', 'DELAYED'); END IF; END \$\$;"
    }

    log info 'Test cleanup completed'
}
