# nu-lint-ignore-file: dont_mix_different_effects, max_positional_params, string_may_be_bare, division_to_format_duration, remove_hat_not_builtin, unhandled_external_error

use std/log

const KUBERNETES_VERSION = 'v1.34.7'
const MINIKUBE_CPUS = '6'
const MINIKUBE_DISK = '40g'
const MINIKUBE_MEMORY = '12288'
const NAMESPACE = 'data-proxy'
const PROFILE = 'data-proxy'
const FALLBACK_CACHE_REDIS_DB = '1'
const STREAMS = [dp:extract dp:prepare dp:publish]
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

# Wait for the given resources to roll out.
def wait-for [kind: string, kubecfg: path]: list<string> -> nothing {
    for ref in $in {
        let r = $ref | parse '{namespace}/{name}' | first
        log info $'  ($kind)/($r.namespace)/($r.name)…'
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

# Wait until the fresh Minikube control plane remains stable.
def wait-for-control-plane [kubecfg: path]: nothing -> nothing {
    mut ready = false
    for attempt in 1..60 {
        if (k $kubecfg get --raw /readyz | complete).exit_code == 0 {
            $ready = true
            break
        }
        sleep 5sec
    }
    if not $ready {
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

# Wait until metrics-server serves the resource metrics API.
def wait-for-metrics [kubecfg: path]: nothing -> nothing {
    (['kube-system/metrics-server'] | wait-for deployment $kubecfg) | ignore

    mut ready = false
    for attempt in 1..60 {
        if (k $kubecfg get --raw /apis/metrics.k8s.io/v1beta1/nodes | complete).exit_code == 0 {
            $ready = true
            break
        }
        sleep 5sec
    }

    if not $ready {
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
        {image: 'data-proxy:local', dockerfile: 'Dockerfile'}
        {image: 'localhost/data-proxy-postgres:17.0.0-local', dockerfile: 'Dockerfile.postgres'}
        {image: 'data-proxy-nginx-proxy:local', dockerfile: 'Dockerfile.proxy'}
        {image: 'data-proxy-nushell:local', dockerfile: 'Dockerfile.nushell'}
        {image: 'localhost/k6:local', dockerfile: 'Dockerfile.k6'}
        {image: 'localhost/oidc:local', dockerfile: 'Dockerfile.oidc'}
    ]
    | each {|img|
        log info $'Building ($img.image)…'
        docker build -t $img.image -f $img.dockerfile .
        log info $'Loading ($img.image) into Minikube…'
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

# Empty the SeaweedFS bucket and clear Redis and Postgres so the next k6 test
# starts from a clean baseline.
def clear-test-resources [kubecfg: path]: nothing -> nothing {
    (weed
        $kubecfg
        's3.bucket.delete -name test-bucket'
        's3.bucket.create -name test-bucket'
    )

    let stale_jobs = (
        k $kubecfg -n data-proxy get jobs -o name
        | lines
        | where $it =~ 'seeder|publisher'
    )

    if ($stale_jobs | is-not-empty) {
        k $kubecfg -n data-proxy delete ...$stale_jobs --ignore-not-found
    }

    let jsonpath = 'jsonpath={.items[0].metadata.name}'
    let valkey = (
        (k $kubecfg -n data-proxy get pod -l app.kubernetes.io/name=valkey -o $jsonpath)
        | str trim
    )

    for stream in $STREAMS {
        (k
            $kubecfg
            -n
            data-proxy
            exec
            $valkey
            --
            redis-cli
            XTRIM
            $stream
            MAXLEN
            0
        )
    }

    log info $'Clearing fallback response cache in Redis DB ($FALLBACK_CACHE_REDIS_DB)…'
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

    (k
        $kubecfg
        -n
        data-proxy
        exec
        $valkey
        --
        sh
        -c
        'redis-cli --scan --pattern "dp:state:*" | xargs -r redis-cli DEL; redis-cli --scan --pattern "dp:plans:*" | xargs -r redis-cli DEL; redis-cli --scan --pattern "dp:results:*" | xargs -r redis-cli DEL; redis-cli --scan --pattern "dp:remaining:*" | xargs -r redis-cli DEL; redis-cli DEL dp:active'
    )

    let pg = (k
        $kubecfg
        -n
        data-proxy
        get
        pod
        -l
        cnpg.io/cluster=data-proxy
        -l
        cnpg.io/instanceRole=primary
        -o
        $jsonpath
    ) | str trim

    let tables = (
        k $kubecfg -n data-proxy exec $pg -- psql postgresql://dataproxy:test-pg-pass@data-proxy-rw:5432/data-proxy -t -A -c "SELECT tablename FROM pg_tables WHERE schemaname = 'pic' AND tablename NOT IN ('freshness', 'access_policy')"
    )

    if ($tables | str trim | is-not-empty) {
        let drop_stmt = (
            $tables
            | lines
            | each {|t| $'DROP TABLE IF EXISTS pic."($t | str trim)" CASCADE' }
            | str join '; '
        )
        (
            k $kubecfg -n data-proxy exec $pg -- psql postgresql://dataproxy:test-pg-pass@data-proxy-rw:5432/data-proxy -c $"($drop_stmt); DELETE FROM partman.part_config WHERE parent_table LIKE 'pic.%'; DELETE FROM pic.freshness; DELETE FROM pic.access_policy;"
        )
    } else {
        (
            k $kubecfg -n data-proxy exec $pg -- psql postgresql://dataproxy:test-pg-pass@data-proxy-rw:5432/data-proxy -c "DELETE FROM partman.part_config WHERE parent_table LIKE 'pic.%'; DELETE FROM pic.freshness; DELETE FROM pic.access_policy;"
        )
    }
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
    log info $'Creating configmap ($configmap)…'
    (k
        $kubecfg
        -n
        data-proxy
        create
        configmap
        $configmap
        --from-file=($script_key + '=' + $script_path)
        --from-file=lib.ts=k6/lib.ts
        --dry-run=client
        -o
        yaml
    ) | k $kubecfg apply -f -

    log info $'Deleting previous testrun ($testrun)…'
    k $kubecfg -n data-proxy delete testrun $testrun --ignore-not-found

    log info $'Applying testrun ($testrun)…'
    if ($profile != '') or ($migration_phase != '') {
        let yaml = try { open --raw $yaml_path } catch {|err| error make {
            msg: $'Failed to open ($yaml_path): ($err.msg)'
            label: {
                text: $yaml_path
                span: (metadata $yaml_path).span
            }
        } }

        mut rendered = $yaml
        if $profile != '' {
            $rendered = $rendered | str replace --all 'value: load' $'value: ($profile)'
        }
        if $migration_phase != '' {
            $rendered = $rendered | str replace 'value: shared-baseline' $'value: ($migration_phase)'
        }
        $rendered | k $kubecfg apply -f -
    } else {
        k $kubecfg apply -f $yaml_path
    }

    let label = $'k6_cr=($testrun),runner=true'
    let items_jsonpath = 'jsonpath={.items}'
    let complete_jsonpath = '{range .items[*]}{.status.conditions[?(@.type=="Complete")].status}{.status.conditions[?(@.type=="Failed")].status}{end}'
    let pod_jsonpath = 'jsonpath={.items[0].metadata.name}'

    log info 'Waiting for the runner job to appear…'
    mut job_ready = false
    for attempt in 1..300 {
        let jobs = k $kubecfg -n data-proxy get jobs -l $label -o $items_jsonpath | str trim
        if ($jobs | is-not-empty) and ($jobs != '[]') {
            $job_ready = true
            break
        }
        sleep 1sec
    }
    if not $job_ready {
        error make {
            msg: 'k6 runner Job did not appear within 5 minutes'
            label: {
                text: 'k6-run'
                span: (metadata $testrun).span
            }
        }
    }

    log info 'Waiting for the test to complete…'
    mut complete = false
    mut failed = false
    for attempt in 1..1800 {
        let phase = (
            (k $kubecfg -n data-proxy get jobs -l $label -o $'jsonpath=($complete_jsonpath)')
            | str trim
        )
        if $phase == 'True' {
            $complete = true
            break
        }
        if $phase == 'FalseTrue' {
            $failed = true
            break
        }
        sleep 2sec
    }

    let pod = (k $kubecfg -n data-proxy get pods -l $label -o $pod_jsonpath)
    let runner_log = (k $kubecfg -n data-proxy logs $pod)
    print ($runner_log | to text)

    if $failed {
        error make {
            msg: 'k6 runner Job failed'
            label: {
                text: 'k6-run'
                span: (metadata $testrun).span
            }
        }
    }
    if not $complete {
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

# Rebuild and roll out the local nginx proxy image.
def refresh-proxy [kubecfg: path]: nothing -> nothing {
    log info 'Building data-proxy-nginx-proxy:local…'
    docker build -q -t data-proxy-nginx-proxy:local -f Dockerfile.proxy .
    docker save -q data-proxy-nginx-proxy:local | mk $kubecfg image load -
    k $kubecfg -n data-proxy rollout restart deployment/data-proxy-nginx-proxy out> /dev/null
    (k
        $kubecfg
        -n
        data-proxy
        rollout
        status
        deployment/data-proxy-nginx-proxy
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

    log info 'Building and loading local images…'
    build-images $kubecfg

    log info 'Waiting for the CNPG controller…'
    ['cnpg-system/cnpg-cloudnative-pg'] | wait-for deployment $kubecfg

    log info 'Applying GCP secret…'
    apply-gcp-secret $kubecfg

    log info 'Deleting the init-db Job so it recreates the postgres S3 secret…'
    k $kubecfg -n data-proxy delete job data-proxy-init-db --ignore-not-found

    log info 'Upgrading data-proxy release…'
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

    log info 'Waiting for the init-db Job…'
    (k
        $kubecfg
        -n
        data-proxy
        wait
        --for=condition=complete
        job/data-proxy-init-db
        --timeout=180s
    ) out> /dev/null

    log info 'Waiting for data-proxy deployments…'
    [
        'data-proxy/data-proxy-nginx-proxy'
        'data-proxy/data-proxy-postgrest-ro'
        'data-proxy/data-proxy-postgrest-rw'
    ] | wait-for deployment $kubecfg

    log info 'Clearing test resources…'
    clear-test-resources $kubecfg

    log info 'Running e2e test…'
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

    log info 'Validating the shared baseline…'
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

    log info 'Upgrading data-proxy to HA…'
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

    log info 'Reconciling settled HA topology…'
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

    log info 'Downgrading data-proxy to shared mode…'
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

    log info 'Reconciling settled shared topology…'
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

# Start Minikube and install the complete local stack.
def "main up" []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig

    let repo = git-root

    log info 'Starting Minikube…'
    start-minikube $kubecfg

    k $kubecfg wait --for=condition=Ready nodes --all --timeout=5m

    log info 'Waiting for the Minikube control plane…'
    wait-for-control-plane $kubecfg

    log info 'Enabling metrics-server…'
    mk $kubecfg addons enable metrics-server
    log info 'Waiting for metrics-server…'
    wait-for-metrics $kubecfg

    log info 'Building container images…'
    build-images $kubecfg

    log info 'Building Helm dependencies…'
    hm $kubecfg dependency build $'($repo)/helm'

    k $kubecfg create namespace data-proxy --dry-run=client -o yaml | k $kubecfg apply -f -
    log info 'Applying local Redis secret…'
    (
        k $kubecfg -n data-proxy create secret generic data-proxy-redis
            '--from-literal=REDIS_PASSWORD=valkey-local'
            '--from-literal=password=valkey-local'
            '--from-literal=REDIS={"read":"redis://:valkey-local@data-proxy-valkey:6379/1","write":"redis://:valkey-local@data-proxy-valkey:6379/0"}'
            --dry-run=client -o yaml
    ) | k $kubecfg apply -f -

    log info 'Installing platform charts with Helmfile…'
    with-env {KUBECONFIG: $kubecfg} {
        helmfile --concurrency 1 --file ($repo | path join helmfile.yaml) sync --wait --timeout 900
    }

    log info 'Verifying CNPG and KEDA stability…'
    verify-platform $kubecfg

    log info 'Applying GCP secret…'
    apply-gcp-secret $kubecfg

    log info 'Installing data-proxy…'
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

    log info 'Waiting for data-proxy deployments…'
    [
        data-proxy/oidc
        data-proxy/data-proxy-swagger-ui
    ] | wait-for deployment $kubecfg

    log info 'Waiting for CNPG cluster…'
    mut cluster_ready = false
    for attempt in 1..300 {
        let phase = try {
            k $kubecfg -n data-proxy get cluster data-proxy -o json
            | from json
            | get status.phase
        } catch {
            ''
        }
        if $phase == 'Cluster in healthy state' {
            $cluster_ready = true
            break
        }
        sleep 2sec
    }

    if not $cluster_ready {
        error make {
            msg: 'CNPG cluster did not become healthy within 10 minutes'
            label: {
                text: 'main up'
                span: (metadata $kubecfg).span
            }
        }
    }

    log info 'Creating the SeaweedFS test bucket…'
    (weed
        $kubecfg
        's3.bucket.delete -name test-bucket'
        's3.bucket.create -name test-bucket'
    )

    show-status $kubecfg
}

# Remove the Minikube profile.
def "main down" []: nothing -> nothing {
    log info 'Deleting Minikube profile…'
    minikube --profile $PROFILE delete
}

# Script to create a testing environment with minikube
def main []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig

    mk $kubecfg status

    show-status $kubecfg
}
