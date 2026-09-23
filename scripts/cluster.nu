# nu-lint-ignore-file: dont_mix_different_effects, max_positional_params, string_may_be_bare, division_to_format_duration, remove_hat_not_builtin, unhandled_external_error

use std/log
use ./lib.nu [poll]

const KUBERNETES_VERSION = 'v1.34.7'
const MINIKUBE_CPUS = '6'
const MINIKUBE_DISK = '40g'
const MINIKUBE_MEMORY = '12288'
const NAMESPACE = 'data-proxy'
const PROFILE = 'data-proxy'
const FALLBACK_CACHE_REDIS_DB = '1'
const TEST_BUCKET = 'test-bucket'
const TEST_SCHEMA = 'pic'

# Path to the repository git-root.
def git-root []: nothing -> string {
    git rev-parse --show-toplevel | str trim
}

# Wrapped kubectl using the isolated kubeconfig.
def --wrapped k [kubecfg: path, ...rest: string]: string -> string, nothing -> string {
    with-env {KUBECONFIG: $kubecfg} {
        kubectl --context=($PROFILE) ...$rest
    }
}

# Wrapped minikube for the data-proxy profile with the isolated kubeconfig.
def --wrapped mk [kubecfg: path, ...rest: string]: nothing -> string {
    with-env {KUBECONFIG: $kubecfg} {
        minikube --profile $PROFILE ...$rest
    }
}

# Wrapped helm using the isolated kubeconfig.
def --wrapped hm [kubecfg: path, ...rest: string]: nothing -> string {
    with-env {KUBECONFIG: $kubecfg} {
        ^helm --kube-context $PROFILE ...$rest
    }
}

def wait-for [kind: string, kubecfg: path]: list<string> -> nothing {
    for ref in $in {
        let r = $ref | parse '{namespace}/{name}' | first
        log info $'  ($kind)/($r.namespace)/($r.name)...'
        (k
            $kubecfg
            -n
            $r.namespace
            rollout
            status
            $'($kind)/($r.name)'
            --timeout=15m
        )
    }
}

# Start a missing local cluster with the supported Kubernetes version and capacity.
def start-minikube [kubecfg: path]: nothing -> string {
    if (mk $kubecfg status | complete).exit_code != 0 {
        (
            (mk
                $kubecfg
                start
                --driver=podman
                --container-runtime=containerd
                --kubernetes-version
                $KUBERNETES_VERSION
                --cpus
                $MINIKUBE_CPUS
                --memory
                $MINIKUBE_MEMORY
                --disk-size
                $MINIKUBE_DISK
            )
        )
    }

    mk $kubecfg update-context
}

# Check whether the Kubernetes API server readyz endpoint responds.
def wait-for-control-plane [kubecfg: path]: nothing -> nothing {
    if not (poll {|| ((k $kubecfg get --raw /readyz | complete).exit_code == 0) } {interval: 5sec, max_attempts: 60}) {
        error make {
            msg: 'Kubernetes API server did not become ready within 5 minutes'
            label: {
                text: 'wait-for-control-plane'
                span: (metadata $kubecfg).span
            }
        }
    }

    (
        (k
            $kubecfg
            -n
            kube-system
            wait
            --for=condition=Ready
            pod
            -l
            component=etcd
            --timeout=5m
        )
    ) | ignore
    (
        (k
            $kubecfg
            -n
            kube-system
            wait
            --for=condition=Ready
            pod/storage-provisioner
            --timeout=5m
        )
    ) | ignore

    let before = restart-count $kubecfg kube-system 'integration-test=storage-provisioner'
    sleep 30sec
    let after = restart-count $kubecfg kube-system 'integration-test=storage-provisioner'
    if $after != $before {
        error make {
            msg: 'storage-provisioner restarted during the control-plane stability window'
            label: {
                text: 'wait-for-control-plane'
                span: (metadata $kubecfg).span
            }
        }
    }
}

# Check whether the metrics-server resource metrics API is available.
def wait-for-metrics [kubecfg: path]: nothing -> nothing {
    (['kube-system/metrics-server'] | wait-for deployment $kubecfg) | ignore

    if not (poll {|| ((k $kubecfg get --raw /apis/metrics.k8s.io/v1beta1/nodes | complete).exit_code == 0) } {interval: 5sec, max_attempts: 60}) {
        error make {
            msg: 'metrics-server did not become ready within 5 minutes'
            label: {
                text: 'wait-for-metrics'
                span: (metadata $kubecfg).span
            }
        }
    }
}

# Return the total restart count for matching Pods.
def restart-count [kubecfg: path, namespace: string, selector: string]: nothing -> int {
    let pods = try {
        k $kubecfg -n $namespace get pods -l $selector -o json
        | from json
        | get items
    } catch {
        return 0
    }

    if ($pods | is-empty) { return 0 }

    $pods
    | each {|pod|
        $pod.status.containerStatuses
        | default []
        | each {|container| $container.restartCount | into int }
        | math sum
    }
    | math sum
}

# Return whether a Service has a ready EndpointSlice address.
def service-ready [kubecfg: path, namespace: string, service: string]: nothing -> bool {
    let slices = try {
        (k
            $kubecfg
            -n
            $namespace
            get
            endpointslice
            -l
            $'kubernetes.io/service-name=($service)'
            -o
            json
        )
        | from json
        | get items
    } catch {
        return false
    }

    $slices
    | any {|slice|
        ($slice.endpoints | default [] | any {|endpoint| $endpoint.conditions.ready })
    }
}

# Verify that platform controllers and their webhooks remain stable.
def verify-platform [kubecfg: path]: nothing -> nothing {
    [
        'cnpg-system/cnpg-cloudnative-pg'
        'keda/keda-operator'
        'keda/keda-operator-metrics-apiserver'
        'keda/keda-admission-webhooks'
    ] | wait-for deployment $kubecfg | ignore

    for webhook in [
        {namespace: 'cnpg-system', name: 'cnpg-webhook-service'}
        {namespace: 'keda', name: 'keda-admission-webhooks'}
    ] {
        if not (service-ready $kubecfg $webhook.namespace $webhook.name) {
            error make {
                msg: $'Webhook service ($webhook.namespace)/($webhook.name) has no endpoint'
                label: {
                    text: 'verify-platform'
                    span: (metadata $kubecfg).span
                }
            }
        }
    }

    let before_cnpg = restart-count $kubecfg cnpg-system 'app.kubernetes.io/name=cloudnative-pg'
    let before_keda = restart-count $kubecfg keda 'app.kubernetes.io/name=keda-operator'
    let before_keda_metrics = restart-count $kubecfg keda 'app.kubernetes.io/name=keda-metrics-apiserver'
    let before_keda_webhook = restart-count $kubecfg keda 'app.kubernetes.io/name=keda-admission-webhooks'
    let before_storage = restart-count $kubecfg kube-system 'integration-test=storage-provisioner'
    sleep 30sec
    let after_cnpg = restart-count $kubecfg cnpg-system 'app.kubernetes.io/name=cloudnative-pg'
    let after_keda = restart-count $kubecfg keda 'app.kubernetes.io/name=keda-operator'
    let after_keda_metrics = restart-count $kubecfg keda 'app.kubernetes.io/name=keda-metrics-apiserver'
    let after_keda_webhook = restart-count $kubecfg keda 'app.kubernetes.io/name=keda-admission-webhooks'
    let after_storage = restart-count $kubecfg kube-system 'integration-test=storage-provisioner'

    if $after_cnpg != $before_cnpg or $after_keda != $before_keda or $after_keda_metrics != $before_keda_metrics or $after_keda_webhook != $before_keda_webhook or $after_storage != $before_storage {
        error make {
            msg: 'CNPG or KEDA restarted during the platform stability window'
            label: {
                text: 'verify-platform'
                span: (metadata $kubecfg).span
            }
        }
    }
}

# Build the platform container images into Minikube.
def --env build-images [kubecfg: path]: nothing -> string {
    let repo = git-root
    try { cd $repo } catch {|err|
        log error $'cd failed: ($err.msg)'
        return
    }

    [
        {image: 'data-proxy-pipeline:local', dockerfile: 'Dockerfile.pipeline'}
        {image: 'localhost/data-proxy-postgres:17.0.0-local', dockerfile: 'Dockerfile.postgres'}
        {image: 'data-proxy-proxy:local', dockerfile: 'Dockerfile.proxy'}
        {image: 'data-proxy-nushell:local', dockerfile: 'Dockerfile.nushell'}
        {image: 'localhost/k6:local', dockerfile: 'Dockerfile.k6'}
        {image: 'localhost/oidc:local', dockerfile: 'Dockerfile.oidc'}
    ]
    | each {|img|
        log info $'Building ($img.image)...'
        docker build -t $img.image -f $img.dockerfile .
        log info $'Loading ($img.image) into Minikube...'
        docker save $img.image | mk $kubecfg image load -
    }
}

# Apply the GCP service-account key as a Kubernetes secret.
def apply-gcp-secret [kubecfg: path]: nothing -> string {
    let creds = $env.HOME | path join .config/gcloud/application_default_credentials.json

    if not ($creds | path exists) {
        log warning 'GCP credentials not found, skipping secret'
        return
    }

    (
        k $kubecfg -n data-proxy create secret generic gcp-key $'--from-file=key.json=($creds)' --dry-run=client -o yaml
    )
    | k $kubecfg apply -f -
}

# Run weed shell commands inside the SeaweedFS all-in-one pod.
def weed [kubecfg: path, ...commands: string]: nothing -> nothing {
    let jsonpath = 'jsonpath={.items[0].metadata.name}'

    let pod = (
        (k
            $kubecfg
            -n
            data-proxy
            get
            pod
            -l
            app.kubernetes.io/component=seaweedfs-all-in-one
            -o
            $jsonpath
        )
        | str trim
    )

    ($commands
        | str join (char nl)
        | kubectl --kubeconfig=($kubecfg) --context=($PROFILE) -n data-proxy exec -i $pod -- weed shell -master=localhost:9333
    ) | ignore
}

# Empty the SeaweedFS bucket and clear response cache and database state so the next k6 test starts from a clean baseline.
def clear-test-resources [kubecfg: path]: nothing -> nothing {
    (weed
        $kubecfg
        's3.bucket.delete -name test-bucket'
        's3.bucket.create -name test-bucket'
    )

    let stale_jobs = (
        k $kubecfg -n data-proxy get jobs -o name
        | lines
        | where $it =~ 'data-proxy-(e2e|sync-k6|workflow-k6)-'
    )

    if ($stale_jobs | is-not-empty) {
        k $kubecfg -n data-proxy delete ...$stale_jobs --ignore-not-found
    }

    let jsonpath = 'jsonpath={.items[0].metadata.name}'
    let valkey = (
        (k $kubecfg -n data-proxy get pod -l app.kubernetes.io/name=valkey -o $jsonpath)
        | str trim
    )

    log info $'Clearing fallback response cache in Redis DB ($FALLBACK_CACHE_REDIS_DB)...'
    (k
        $kubecfg
        -n
        data-proxy
        exec
        $valkey
        --
        redis-cli
        -n
        $FALLBACK_CACHE_REDIS_DB
        FLUSHDB
    )

    let clusters = try {
        k $kubecfg -n data-proxy get clusters -o json
        | from json
        | get items
        | get metadata.name
        | where $it !~ '-dbos$'
    } catch {|err| error make {
        msg: $'Failed to read CNPG clusters: ($err.msg)'
        label: {
            text: clear-test-resources
            span: (metadata $kubecfg).span
        }
    } }

    for cluster in $clusters {
        let schema = if $cluster == 'data-proxy' {
            $TEST_SCHEMA
        } else {
            $cluster | str replace 'data-proxy-' ''
        }
        let pg = (
            k $kubecfg -n data-proxy get pod
                -l $'cnpg.io/cluster=($cluster)'
                -l cnpg.io/instanceRole=primary
                -o $jsonpath
        ) | str trim
        let dsn = $'postgresql://data-proxy:test-pg-pass@($cluster)-rw:5432/data-proxy'
        let tables = (
            k $kubecfg -n data-proxy exec $pg -- psql $dsn -t -A -c $'SELECT tablename FROM pg_tables WHERE schemaname = \'($schema)\' AND tablename NOT IN (\'freshness\', \'access_policy\')'
        )

        let drop_stmt = (
            $tables
            | lines
            | each {|table| $'DROP TABLE IF EXISTS ($schema)."($table | str trim)" CASCADE' }
            | str join '; '
        )
        let cleanup = $'($drop_stmt); DELETE FROM partman.part_config WHERE parent_table LIKE \'($schema).%\'; DELETE FROM ($schema).freshness; DELETE FROM ($schema).access_policy;'
        (k
            $kubecfg
            -n
            data-proxy
            exec
            $pg
            --
            psql
            $dsn
            -v
            ON_ERROR_STOP=1
            -c
            $cleanup
        )
    }

    let dbos_clusters = (
        k $kubecfg -n data-proxy get clusters -o name
        | lines
        | where $it == 'cluster.postgresql.cnpg.io/data-proxy-dbos'
    )
    if ($dbos_clusters | is-not-empty) {
        let dbos_pg = (
            k $kubecfg -n data-proxy get pod
                -l cnpg.io/cluster=data-proxy-dbos
                -l cnpg.io/instanceRole=primary
                -o $jsonpath
        ) | str trim
        let dbos_dsn = 'postgresql://data-proxy:test-pg-pass@data-proxy-dbos-rw:5432/data-proxy'
        (k
            $kubecfg
            -n
            data-proxy
            exec
            $dbos_pg
            --
            psql
            $dbos_dsn
            -v
            ON_ERROR_STOP=1
            -c
            'DELETE FROM data_proxy.state; DELETE FROM data_proxy.errors;'
        )
    }
}

# Check whether the k6 runner job has appeared.
def runner-job-ready [kubecfg: path, label: string]: nothing -> bool {
    let jobs = k $kubecfg -n data-proxy get jobs -l $label -o 'jsonpath={.items}' | str trim
    ($jobs | is-not-empty) and ($jobs != '[]')
}

# Check whether the k6 test job completed, returning completion and failure flags.
def test-phase [kubecfg: path, label: string]: nothing -> record<complete: bool, failed: bool> {
    let complete_path = 'jsonpath={range .items[*]}{.status.conditions[?(@.type=="Complete")].status}{.status.conditions[?(@.type=="Failed")].status}{end}'
    let phase = (k $kubecfg -n data-proxy get jobs -l $label -o $complete_path) | str trim
    if $phase == 'True' { return {complete: true, failed: false} }
    if $phase == 'FalseTrue' { return {complete: false, failed: true} }
    {complete: false, failed: false}
}

# Create a configmap, apply a testrun yaml, wait for completion, and print the runner log.
def k6-run [
    kubecfg: path
    configmap: string
    script_key: string
    script_path: string
    testrun: string
    yaml_path: path
    --profile: string = ''
    --migration-phase: string = ''
]: nothing -> nothing {
    log info $'Creating configmap ($configmap)...'
    (k
        $kubecfg
        -n
        data-proxy
        create
        configmap
        $configmap
        --from-file=($script_key + '=' + $script_path)
        --from-file=lib.ts=k6/lib.ts
        --from-file=kubernetes.ts=k6/types/kubernetes.ts
        --from-file=trigger.py=scripts/trigger.py
        --from-file=inspect_dbos.py=scripts/inspect_dbos.py
        --dry-run=client
        -o
        yaml
    ) | k $kubecfg apply -f -

    log info $'Deleting previous testrun ($testrun)...'
    k $kubecfg -n data-proxy delete testrun $testrun --ignore-not-found

    log info $'Applying testrun ($testrun)...'
    if ($profile != '') or ($migration_phase != '') {
        let yaml = try { open --raw $yaml_path } catch {|err| error make {
            msg: $'Failed to open ($yaml_path): ($err.msg)'
            label: {
                text: $yaml_path
                span: (metadata $yaml_path).span
            }
        } }

        let rendered = $yaml
        | if $profile != '' { str replace --all 'value: load' $'value: ($profile)' } else { }
        | if $migration_phase != '' { str replace 'value: shared-baseline' $'value: ($migration_phase)' } else { }
        $rendered | k $kubecfg apply -f -
    } else {
        k $kubecfg apply -f $yaml_path
    }

    let label = $'k6_cr=($testrun),runner=true'

    log info 'Waiting for the runner job to appear...'
    if not (poll {|| (runner-job-ready $kubecfg $label) } {interval: 1sec, max_attempts: 300}) {
        error make {
            msg: 'k6 runner Job did not appear within 5 minutes'
            label: {
                text: 'k6-run'
                span: (metadata $testrun).span
            }
        }
    }

    log info 'Waiting for the test to complete...'
    let result = poll {|| (test-phase $kubecfg $label) } {interval: 2sec, max_attempts: 1800}

    let pod = (
        (k
            $kubecfg
            -n
            data-proxy
            get
            pods
            -l
            $label
            -o
            'jsonpath={.items[0].metadata.name}'
        )
    )
    let runner_log = (k $kubecfg -n data-proxy logs $pod)
    print ($runner_log | to text)

    if $result.failed {
        error make {
            msg: 'k6 runner Job failed'
            label: {
                text: 'k6-run'
                span: (metadata $testrun).span
            }
        }
    }
    if not $result.complete {
        error make {
            msg: 'k6 runner Job timed out after 60 minutes'
            label: {
                text: 'k6-run'
                span: (metadata $testrun).span
            }
        }
    }
}

# Print cluster status with kubecolor.
def show-status [kubecfg: path]: nothing -> nothing {
    with-env {KUBECONFIG: $kubecfg} {
        kubecolor --context=($PROFILE) -n data-proxy get pods
        print ""
        kubecolor --context=($PROFILE) -n data-proxy get deploy
        print ""
        kubecolor --context=($PROFILE) -n data-proxy get cluster
        print ""
        kubecolor --context=($PROFILE) -n data-proxy get scaledobject
        print ""
        kubecolor --context=($PROFILE) -n data-proxy get scaledjob
    }
}

# Rebuild and roll out the local proxy image.
def refresh-proxy [kubecfg: path]: nothing -> nothing {
    log info 'Building data-proxy-proxy:local...'
    docker build -q -t data-proxy-proxy:local -f Dockerfile.proxy .
    docker save -q data-proxy-proxy:local | mk $kubecfg image load -
    k $kubecfg -n data-proxy rollout restart deployment/data-proxy-proxy out> /dev/null
    (k
        $kubecfg
        -n
        data-proxy
        rollout
        status
        deployment/data-proxy-proxy
        --timeout=180s
    ) out> /dev/null
}

# Run the standard k6 load profile.
def "main k6 load" []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig
    refresh-proxy $kubecfg
    clear-test-resources $kubecfg
    k6-run $kubecfg 'data-proxy-k6' 'load.ts' 'k6/load.ts' 'data-proxy-load' 'k6/load.yaml' --profile 'load'
}

# Run the k6 stress profile.
def "main k6 stress" []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig
    refresh-proxy $kubecfg
    clear-test-resources $kubecfg
    k6-run $kubecfg 'data-proxy-k6' 'load.ts' 'k6/load.ts' 'data-proxy-load' 'k6/load.yaml' --profile 'stress'
}

# Run the e2e test (triggers sync, seeds RLS, validates pipeline).
def "main k6 e2e" []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig

    let repo = git-root
    try { cd $repo } catch {|err|
        log error $'cd failed: ($err.msg)'
        return
    }

    log info 'Building and loading local images...'
    build-images $kubecfg

    log info 'Waiting for the CNPG controller...'
    ['cnpg-system/cnpg-cloudnative-pg'] | wait-for deployment $kubecfg

    log info 'Applying GCP secret...'
    apply-gcp-secret $kubecfg

    log info 'Deleting init-db Jobs so they recreate PostgreSQL setup...'
    let old_init_jobs = (
        k $kubecfg -n data-proxy get jobs -l app.kubernetes.io/component=init-db -o name
        | lines
    )
    if ($old_init_jobs | is-not-empty) {
        k $kubecfg -n data-proxy delete ...$old_init_jobs --ignore-not-found
    }

    log info 'Upgrading data-proxy release...'
    (hm
        $kubecfg
        upgrade
        data-proxy
        $'($repo)/helm'
        --namespace
        data-proxy
        --values
        $'($repo)/scripts/values/data-proxy.yaml'
    )

    log info 'Waiting for the init-db Job...'
    let init_jobs = (
        k $kubecfg -n data-proxy get jobs -l app.kubernetes.io/component=init-db -o name
        | lines
    )
    for job in $init_jobs {
        (
            (k
                $kubecfg
                -n
                data-proxy
                wait
                --for=condition=complete
                $job
                --timeout=180s
            )
        ) out> /dev/null
    }

    log info 'Waiting for data-proxy deployments...'
    [
        'data-proxy/data-proxy-proxy'
        'data-proxy/data-proxy-postgrest-ro'
        'data-proxy/data-proxy-postgrest-rw'
        'data-proxy/data-proxy-pipeline'
    ] | wait-for deployment $kubecfg

    log info 'Clearing test resources...'
    clear-test-resources $kubecfg

    log info 'Running e2e test...'
    (k6-run
        $kubecfg
        'data-proxy-e2e'
        'e2e.ts'
        'k6/e2e.ts'
        'data-proxy-e2e'
        'k6/e2e.yaml'
    )
}

# Recover from a failed or pending migration before starting a new test.
def recover-migration [kubecfg: path, repo: path]: nothing -> nothing {
    let status = (
        hm $kubecfg status data-proxy --namespace data-proxy
        | lines
        | first
        | split words
        | last
        | str trim
    )
    let mode = (
        (k
            $kubecfg
            -n
            data-proxy
            get
            configmap
            data-proxy-mode-state
            -o
            jsonpath='{.data.mode}'
        )
        | str trim
    )
    let state = (
        (k
            $kubecfg
            -n
            data-proxy
            get
            configmap
            data-proxy-mode-state
            -o
            jsonpath='{.data.status}'
        )
        | str trim
    )

    if $status == 'deployed' and $mode == 'shared' and $state == 'completed' {
        return
    }

    if $status != 'deployed' {
        let direction = (
            (k
                $kubecfg
                -n
                data-proxy
                get
                configmap
                data-proxy-mode-state
                -o
                jsonpath='{.data.direction}'
            )
            | str trim
        )
        let revision = (
            hm $kubecfg history data-proxy --namespace data-proxy
            | lines
            | last
            | split words
            | first
            | into int
        )
        (k
            $kubecfg
            -n
            data-proxy
            delete
            secret
            $'sh.helm.release.v1.data-proxy.v($revision)'
            --ignore-not-found
        )

        if $direction == 'to-ha' {
            (k
                $kubecfg
                -n
                data-proxy
                patch
                configmap
                data-proxy-mode-state
                --type
                merge
                -p
                '{"data":{"mode":"per-schema","status":"completed","direction":"to-ha"}}'
            )
            (
                (hm
                    $kubecfg
                    upgrade
                    --take-ownership
                    data-proxy
                    $'($repo)/helm'
                    --namespace
                    data-proxy
                    --values
                    $'($repo)/scripts/values/data-proxy.yaml'
                    --values
                    $'($repo)/scripts/values/data-proxy-ha.yaml'
                )
            ) | ignore
            (
                (hm
                    $kubecfg
                    upgrade
                    data-proxy
                    $'($repo)/helm'
                    --namespace
                    data-proxy
                    --values
                    $'($repo)/scripts/values/data-proxy.yaml'
                )
            ) | ignore
        } else {
            (k
                $kubecfg
                -n
                data-proxy
                patch
                configmap
                data-proxy-mode-state
                --type
                merge
                -p
                '{"data":{"mode":"shared","status":"completed","direction":"none"}}'
            )
            (
                (hm
                    $kubecfg
                    upgrade
                    data-proxy
                    $'($repo)/helm'
                    --namespace
                    data-proxy
                    --values
                    $'($repo)/scripts/values/data-proxy.yaml'
                )
            ) | ignore
        }
    }
}

# Validate published data through a full shared → HA → shared migration round trip.
def "main k6 migrate" []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig
    let repo = git-root

    recover-migration $kubecfg $repo

    main k6 e2e

    log info 'Validating the shared baseline...'
    (k
        $kubecfg
        -n
        data-proxy
        delete
        configmap
        data-proxy-migration-fingerprint
        --ignore-not-found
    )
    k6-run $kubecfg 'data-proxy-migration' 'migration.ts' 'k6/migration.ts' 'data-proxy-migration' 'k6/migration.yaml' --migration-phase 'shared-baseline'

    log info 'Upgrading data-proxy to HA...'
    (
        (hm
            $kubecfg
            upgrade
            data-proxy
            $'($repo)/helm'
            --namespace
            data-proxy
            --values
            $'($repo)/scripts/values/data-proxy.yaml'
            --values
            $'($repo)/scripts/values/data-proxy-ha.yaml'
        )
    ) | ignore
    let ha_job = (
        (k
            $kubecfg
            -n
            data-proxy
            get
            jobs
            -l
            app.kubernetes.io/component=migrate
            --sort-by=.metadata.creationTimestamp
            -o
            name
        )
        | lines
        | last
        | str trim
    )
    k $kubecfg -n data-proxy wait --for=condition=complete $ha_job --timeout=6m
    k6-run $kubecfg 'data-proxy-migration' 'migration.ts' 'k6/migration.ts' 'data-proxy-migration' 'k6/migration.yaml' --migration-phase 'ha'

    log info 'Reconciling settled HA topology...'
    (
        (hm
            $kubecfg
            upgrade
            data-proxy
            $'($repo)/helm'
            --namespace
            data-proxy
            --values
            $'($repo)/scripts/values/data-proxy.yaml'
            --values
            $'($repo)/scripts/values/data-proxy-ha.yaml'
        )
    ) | ignore
    k6-run $kubecfg 'data-proxy-migration' 'migration.ts' 'k6/migration.ts' 'data-proxy-migration' 'k6/migration.yaml' --migration-phase 'ha-settled'

    log info 'Downgrading data-proxy to shared mode...'
    (
        (hm
            $kubecfg
            upgrade
            data-proxy
            $'($repo)/helm'
            --namespace
            data-proxy
            --values
            $'($repo)/scripts/values/data-proxy.yaml'
        )
    ) | ignore
    let shared_job = (
        (k
            $kubecfg
            -n
            data-proxy
            get
            jobs
            -l
            app.kubernetes.io/component=migrate
            --sort-by=.metadata.creationTimestamp
            -o
            name
        )
        | lines
        | last
        | str trim
    )
    (k
        $kubecfg
        -n
        data-proxy
        wait
        --for=condition=complete
        $shared_job
        --timeout=6m
    )
    k6-run $kubecfg 'data-proxy-migration' 'migration.ts' 'k6/migration.ts' 'data-proxy-migration' 'k6/migration.yaml' --migration-phase 'shared-return'

    log info 'Reconciling settled shared topology...'
    (
        (hm
            $kubecfg
            upgrade
            data-proxy
            $'($repo)/helm'
            --namespace
            data-proxy
            --values
            $'($repo)/scripts/values/data-proxy.yaml'
        )
    ) | ignore
    k6-run $kubecfg 'data-proxy-migration' 'migration.ts' 'k6/migration.ts' 'data-proxy-migration' 'k6/migration.yaml' --migration-phase 'shared-settled'
}

# Check whether the CNPG cluster reports a healthy phase.
def cnpg-healthy [kubecfg: path]: nothing -> bool {
    let phase = try {
        k $kubecfg -n data-proxy get cluster data-proxy -o json
        | from json
        | get status.phase
    } catch {
        ''
    }
    $phase == 'Cluster in healthy state'
}

# Start Minikube and install the complete local stack.
def "main up" []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig

    let repo = git-root

    log info 'Starting Minikube...'
    start-minikube $kubecfg

    k $kubecfg wait --for=condition=Ready nodes --all --timeout=5m

    log info 'Waiting for the Minikube control plane...'
    wait-for-control-plane $kubecfg

    log info 'Enabling metrics-server...'
    mk $kubecfg addons enable metrics-server
    log info 'Waiting for metrics-server...'
    wait-for-metrics $kubecfg

    log info 'Building container images...'
    build-images $kubecfg

    log info 'Building Helm dependencies...'
    hm $kubecfg dependency build $'($repo)/helm'

    k $kubecfg create namespace data-proxy --dry-run=client -o yaml | k $kubecfg apply -f -
    log info 'Applying local Redis secret...'
    (
        k $kubecfg -n data-proxy create secret generic data-proxy-redis
            '--from-literal=REDIS_PASSWORD=valkey-local'
            '--from-literal=password=valkey-local'
            '--from-literal=REDIS={"read":"redis://:valkey-local@data-proxy-valkey:6379/1","write":"redis://:valkey-local@data-proxy-valkey:6379/0"}'
            --dry-run=client -o yaml
    ) | k $kubecfg apply -f -

    log info 'Installing platform charts with Helmfile...'
    with-env {KUBECONFIG: $kubecfg} {
        helmfile --concurrency 1 --file ($repo | path join helmfile.yaml) sync --wait --timeout 900
    }

    log info 'Verifying CNPG and KEDA stability...'
    verify-platform $kubecfg

    log info 'Applying GCP secret...'
    apply-gcp-secret $kubecfg

    log info 'Installing data-proxy...'
    (hm
        $kubecfg
        upgrade
        --install
        data-proxy
        $'($repo)/helm'
        --namespace
        data-proxy
        --values
        $'($repo)/scripts/values/data-proxy.yaml'
    )

    log info 'Waiting for data-proxy deployments...'
    [
        data-proxy/oidc
        data-proxy/data-proxy-swagger-ui
    ] | wait-for deployment $kubecfg

    log info 'Waiting for CNPG cluster...'
    if not (poll {|| (cnpg-healthy $kubecfg) } {interval: 2sec, max_attempts: 300}) {
        error make {
            msg: 'CNPG cluster did not become healthy within 10 minutes'
            label: {
                text: 'main up'
                span: (metadata $kubecfg).span
            }
        }
    }

    log info 'Creating the SeaweedFS test bucket...'
    (weed
        $kubecfg
        's3.bucket.delete -name test-bucket'
        's3.bucket.create -name test-bucket'
    )

    show-status $kubecfg
}

# Remove the Minikube profile.
def "main down" []: nothing -> nothing {
    log info 'Deleting Minikube profile...'
    minikube --profile $PROFILE delete
}

# Script to create a testing environment with minikube
def main []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig

    mk $kubecfg status

    show-status $kubecfg
}
