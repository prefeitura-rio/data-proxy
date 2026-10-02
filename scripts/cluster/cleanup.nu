
use ../lib.nu [fail]
use ./lib.nu [wrap-kubectl namespace test-schema]

# Clear E2E Jobs, response cache, test tables, and pending DBOS workflows.
export def clear-test-resources [kubecfg: path]: nothing -> nothing {
    let stale_jobs = wrap-kubectl $kubecfg -n (namespace) get jobs -o name
    | lines
    | where $it =~ 'data-proxy-(e2e|sync-k6|workflow-k6)-'

    if ($stale_jobs | is-not-empty) {
        wrap-kubectl $kubecfg -n (namespace) delete ...$stale_jobs --ignore-not-found
    }

    let jsonpath = 'jsonpath={.items[0].metadata.name}'
    let valkey = wrap-kubectl $kubecfg -n (namespace) get pod -l app.kubernetes.io/name=valkey -o $jsonpath
    | str trim

    log info 'Clearing proxy response cache in Redis DB 1...'
    wrap-kubectl $kubecfg -n (namespace) exec $valkey -- redis-cli -n 1 FLUSHDB

    let clusters = try {
        wrap-kubectl $kubecfg -n (namespace) get clusters -o json
        | from json
        | get items
        | get metadata.name
        | where $it !~ '-dbos$'
    } catch {|err|
        fail $'Failed to read CNPG clusters: ($err.msg)' {
            command: clear-test-resources
            span: (metadata $kubecfg).span
        }
    }

    for cluster in $clusters {
        let schema = if $cluster == 'data-proxy' {
            test-schema
        } else {
            $cluster | str replace 'data-proxy-' ''
        }

        let primary = wrap-kubectl $kubecfg -n (namespace) get pod -l $'cnpg.io/cluster=($cluster)' -l cnpg.io/instanceRole=primary -o $jsonpath
        | str trim

        let dsn = $'postgresql://admin:test-pg-pass@($cluster)-rw:5432/data-proxy'
        let query = [
            "SELECT tablename FROM pg_tables WHERE schemaname = '"
            $schema
            "' AND tablename NOT IN ('access_policy', 'access_log')"
        ] | str join ''

        let tables = wrap-kubectl $kubecfg -n (namespace) exec $primary -- psql $dsn -t -A -c $query

        let drop_tables = $tables
        | lines
        | each {|table| $'DROP TABLE IF EXISTS ($schema)."($table | str trim)" CASCADE' }
        | str join '; '

        let cleanup = $'($drop_tables); DELETE FROM ($schema).access_policy;'

        wrap-kubectl $kubecfg -n (namespace) exec $primary -- psql $dsn -v ON_ERROR_STOP=1 -c $cleanup

        if $cluster == 'data-proxy' {
            let state_tables = wrap-kubectl $kubecfg -n (namespace) exec $primary -- psql $dsn -t -A -c "SELECT to_regclass('data_proxy.state') IS NOT NULL AND to_regclass('data_proxy.errors') IS NOT NULL"
            | str trim

            if $state_tables == 't' {
                wrap-kubectl $kubecfg -n (namespace) exec $primary -- psql $dsn -v ON_ERROR_STOP=1 -c 'DELETE FROM data_proxy.state; DELETE FROM data_proxy.errors;'
            }

            wrap-kubectl $kubecfg -n (namespace) exec $primary -- psql $dsn -v ON_ERROR_STOP=1 -c "UPDATE dbos.workflow_status SET status = 'CANCELLED', error = 'Cancelled before test run' WHERE application_name = 'data-proxy-sync' AND status IN ('PENDING', 'ENQUEUED', 'DELAYED');"
        }
    }

    let dbos_cluster = wrap-kubectl $kubecfg -n (namespace) get clusters -o name
    | lines
    | where $it == 'cluster.postgresql.cnpg.io/data-proxy-dbos'

    if ($dbos_cluster | is-not-empty) {
        let primary = wrap-kubectl $kubecfg -n (namespace) get pod -l cnpg.io/cluster=data-proxy-dbos -l cnpg.io/instanceRole=primary -o $jsonpath
        | str trim
        let dsn = 'postgresql://admin:test-pg-pass@data-proxy-dbos-rw:5432/data-proxy'
        wrap-kubectl $kubecfg -n (namespace) exec $primary -- psql $dsn -v ON_ERROR_STOP=1 -c 'DELETE FROM data_proxy.state; DELETE FROM data_proxy.errors;'
    }
}
