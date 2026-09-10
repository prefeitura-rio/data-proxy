# nu-lint-ignore-file: dont_mix_different_effects, max_positional_params, string_may_be_bare, division_to_format_duration, remove_hat_not_builtin, unhandled_external_error, where_closure_drop_parameter

use std/log

const PROFILE = 'data-proxy'

# Path to the repository git-root.
def git-root []: nothing -> string {
    git rev-parse --show-toplevel | str trim
}

# Format a duration as a human-readable string, dropping sub-second precision.
def format-age [d: duration]: nothing -> string {
    let total_sec = $d / 1sec | into int
    let hr = ($total_sec // 3600)
    let min = (($total_sec mod 3600) // 60)
    let sec = (($total_sec mod 3600) mod 60)
    [
        [$hr hr]
        [$min min]
        [$sec sec]
    ]
    | each {|p| if $p.0 > 0 { $"($p.0)($p.1)" } }
    | flatten
    | str join " "
}

# Wrapped kubectl using the isolated kubeconfig.
def --wrapped kc [kubecfg: path, ...rest: string]: string -> string, nothing -> string {
    kubectl --kubeconfig=($kubecfg) --context=($PROFILE) ...$rest
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
        (kc
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

# Install a Helm release from a record spec.
def helm [spec: record, kubecfg: path]: nothing -> string {
    let create_namespace = if ($spec.create_namespace? | default false) { [--create-namespace] } else { [] }

    let version = if $spec.version? != null { [--version $spec.version] } else { [] }

    let values = if $spec.values? != null { [--values $spec.values] } else { [] }

    let set = if $spec.set? != null { [--set $spec.set] } else { [] }

    let opts = [$create_namespace $version $values $set] | flatten

    let args = [install $spec.name $spec.chart --namespace $spec.namespace] ++ $opts

    hm $kubecfg ...$args
}

# Start Minikube if it is not already running.
def start-minikube [kubecfg: path]: nothing -> string {
    if (mk $kubecfg status | complete).exit_code != 0 {
        (
            (mk
                $kubecfg
                start
                --driver=podman
                --container-runtime=containerd
                --cpus=6
                --memory=12288
                --disk-size=40g
            )
        )
    }

    mk $kubecfg update-context
}

# Build the platform container images into Minikube.
def --env build-images [kubecfg: path]: nothing -> string {
    let repo = git-root
    try { cd $repo } catch {|err|
        log error $'cd failed: ($err.msg)'
        return
    }

    log info 'Building data-proxy-postgres:local…'
    docker build -t data-proxy-postgres:local -f Dockerfile.postgres .
    log info 'Loading data-proxy-postgres:local into Minikube…'
    docker save data-proxy-postgres:local | mk $kubecfg image load -

    log info 'Compiling the proxy script…'
    tsc -p nginx --noEmit false --outDir nginx/build

    log info 'Building data-proxy-nginx-proxy:local…'
    docker build -t data-proxy-nginx-proxy:local -f Dockerfile.proxy .
    log info 'Loading data-proxy-nginx-proxy:local into Minikube…'
    docker save data-proxy-nginx-proxy:local | mk $kubecfg image load -

    log info 'Building data-proxy-nushell:local…'
    docker build -t data-proxy-nushell:local -f Dockerfile.nushell .
    log info 'Loading data-proxy-nushell:local into Minikube…'
    docker save data-proxy-nushell:local | mk $kubecfg image load -

    log info 'Building localhost/k6:local…'
    docker build -t localhost/k6:local -f Dockerfile.k6 .
    log info 'Loading localhost/k6:local into Minikube…'
    docker save localhost/k6:local | mk $kubecfg image load -

    log info 'Building localhost/oidc:local…'
    docker build -t localhost/oidc:local -f Dockerfile.oidc .
    log info 'Loading localhost/oidc:local into Minikube…'
    docker save localhost/oidc:local | mk $kubecfg image load -
}

# Install the platform Helm releases.
def install-platform [kubecfg: path]: nothing -> nothing {
    let charts = try { open scripts/charts.nuon } catch {|err|
        log error $'Failed to open charts: ($err.msg)'
        return
    }
    | each {|c|
        if $c has values_file {
            $c | reject values_file | merge {values: $'scripts/values/($c.values_file)'}
        } else { $c }
    }

    $charts
    | where $it has repo
    | select chart repo
    | uniq-by repo
    | each {|c| try { hm $kubecfg repo add ($c.chart | parse '{repo}/{name}' | get repo) $c.repo } catch { null } }

    hm $kubecfg repo update

    for chart in $charts {
        helm $chart $kubecfg
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
        kc $kubecfg -n data-proxy create secret generic gcp-key $'--from-file=key.json=($creds)' --dry-run=client -o yaml
    )
    | kc $kubecfg apply -f -
}

# Run an mc command inside the MinIO pod with credentials pre-configured.
def mc [kubecfg: path, command: string]: nothing -> nothing {
    let pod_jp = 'jsonpath={.items[0].metadata.name}'
    let user_jp = 'jsonpath={.data.root-user}'
    let pass_jp = 'jsonpath={.data.root-password}'

    let minio_pod = (
        (kc $kubecfg -n data-proxy get pod -l app.kubernetes.io/name=minio -o $pod_jp)
        | str trim
    )

    let minio_user = (
        (kc $kubecfg -n data-proxy get secret minio -o $user_jp)
        | decode base64
        | decode utf-8
        | str trim
    )

    let minio_pass = (
        (kc $kubecfg -n data-proxy get secret minio -o $pass_jp)
        | decode base64
        | decode utf-8
        | str trim
    )

    (
        (kc
            $kubecfg
            -n
            data-proxy
            exec
            $minio_pod
            --
            sh
            -c
            $'mc alias set local http://localhost:9000 ($minio_user) ($minio_pass) >/dev/null 2>&1; ($command) >/dev/null 2>&1; true'
        )
    ) | ignore
}

# Create the MinIO test-bucket via the S3 API.
def create-bucket [kubecfg: path]: nothing -> nothing {
    mc $kubecfg 'mc mb --ignore-existing local/test-bucket'
}

# Delete the MinIO test-bucket via the S3 API.
def delete-bucket [kubecfg: path]: nothing -> nothing {
    mc $kubecfg 'mc rb --force --ignore-existing local/test-bucket'
}

# Clear MinIO, Redis, and Postgres so the next k6 test starts from a clean baseline.
def clear-test-resources [kubecfg: path]: nothing -> nothing {
    create-bucket $kubecfg
    mc $kubecfg 'mc rm --recursive --force local/test-bucket'

    let pod_jp = 'jsonpath={.items[0].metadata.name}'
    let pod_jp = 'jsonpath={.items[0].metadata.name}'
    let valkey = (
        (kc $kubecfg -n data-proxy get pod -l app.kubernetes.io/name=valkey -o $pod_jp)
        | str trim
    )

    (kc
        $kubecfg
        -n
        data-proxy
        exec
        $valkey
        --
        redis-cli
        XGROUP
        DESTROY
        dp:extract
        dumpers
    ) | ignore

    (kc
        $kubecfg
        -n
        data-proxy
        exec
        $valkey
        --
        redis-cli
        XGROUP
        DESTROY
        dp:prepare
        seeders
    ) | ignore

    (kc
        $kubecfg
        -n
        data-proxy
        exec
        $valkey
        --
        redis-cli
        XGROUP
        DESTROY
        dp:publish
        publishers
    ) | ignore

    (kc
        $kubecfg
        -n
        data-proxy
        exec
        $valkey
        --
        sh
        -c
        'redis-cli --scan --pattern dp:* | xargs -r redis-cli DEL'
    ) | ignore

    let duckdb = (kc
        $kubecfg
        -n
        data-proxy
        get
        pod
        -l
        app.kubernetes.io/name=data-proxy
        -l
        app.kubernetes.io/component=duckdb
        -o
        $pod_jp
    ) | str trim

    let tables = (
        kc $kubecfg -n data-proxy exec $duckdb -- psql -U dataproxy -d dataproxy -t -A -c "SELECT tablename FROM pg_tables WHERE schemaname = 'pic' AND tablename NOT IN ('freshness', 'access_policy')"
    )

    if ($tables | str trim | is-not-empty) {
        let drop_stmt = (
            $tables
            | lines
            | each {|t| $'DROP TABLE IF EXISTS pic."($t | str trim)" CASCADE' }
            | str join '; '
        )
        (
            kc $kubecfg -n data-proxy exec $duckdb -- psql -U dataproxy -d dataproxy -c $'($drop_stmt); DELETE FROM pic.freshness; DELETE FROM pic.access_policy;'
        ) | ignore
    } else {
        (
            kc $kubecfg -n data-proxy exec $duckdb -- psql -U dataproxy -d dataproxy -c 'DELETE FROM pic.freshness; DELETE FROM pic.access_policy;'
        ) | ignore
    }
}

# Run a k6 load test.
def "main k6 load-test" [
    profile: string = 'smoke'  # smoke, load, or stress
]: nothing -> string {
    let kubecfg = git-root | path join .kubeconfig

    clear-test-resources $kubecfg

    log info 'Creating k6 configmap…'
    (
        (kc
            $kubecfg
            -n
            data-proxy
            create
            configmap
            data-proxy-k6
            --from-file=run.ts=k6/run.ts
            --dry-run=client
            -o
            yaml
        )
    )
    | kc $kubecfg apply -f -

    log info 'Deleting previous testrun…'
    kc $kubecfg -n data-proxy delete testrun data-proxy-load --ignore-not-found

    log info 'Applying testrun…'
    try { open k6/run.yaml } catch {|err|
        log error $'Failed to open k6/run.yaml: ($err.msg)'
        exit 1
    }
    | update spec.runner.env {
        $in | each {|e| if $e.name == K6_PROFILE { $e | update value $profile } else { $e } }
    }
    | to yaml
    | kc $kubecfg apply -f -

    log info 'Watching testrun…'
    kc $kubecfg -n data-proxy get testrun data-proxy-load -w
}

# Wait until the runner job of the e2e testrun reaches a terminal state.
def wait-for-e2e [kubecfg: path]: nothing -> nothing {
    let label = 'k6_cr=data-proxy-e2e,runner=true'
    let items_jp = 'jsonpath={.items}'

    log info 'Waiting for the runner job to appear…'
    while true {
        let jobs = kc $kubecfg -n data-proxy get jobs -l $label -o $items_jp | str trim

        if ($jobs | is-not-empty) and ($jobs != '[]') { break }

        sleep 1sec
    }

    log info 'Waiting for the test to complete…'
    let complete_jp = r#'{range .items[*]}{.status.conditions[?(@.type=="Complete")].status}{.status.conditions[?(@.type=="Failed")].status}{end}'#

    while true {
        let phase = (
            (kc
                $kubecfg
                -n
                data-proxy
                get
                jobs
                -l
                $label
                -o
                $'jsonpath=($complete_jp)'
            )
            | str trim
        )

        if $phase =~ True { break }

        sleep 2sec
    }
}

# Print the runner log and fail when the test run failed.
def print-e2e-logs [kubecfg: path]: nothing -> nothing {
    let label = 'k6_cr=data-proxy-e2e,runner=true'
    let pod_jp = 'jsonpath={.items[0].metadata.name}'
    let failed_jp = r#'{range .items[*]}{.status.conditions[?(@.type=="Failed")].status}{end}'#
    let pod = (kc $kubecfg -n data-proxy get pods -l $label -o $pod_jp)

    (kc $kubecfg -n data-proxy logs $pod) | tee { delete-bucket $kubecfg }

    let failed = (
        kc $kubecfg -n data-proxy get jobs -l $label -o $'jsonpath=($failed_jp)'
        | str trim
    )

    if $failed =~ True {
        error make {
            msg: 'the e2e test run failed; read the runner log above'
            label: {
                text: 'failed condition'
                span: (metadata $failed).span
            }
        }
    }
}

# Fail when the proxy never served an answer from BigQuery.
def check-fallback-served [kubecfg: path, pod: string]: nothing -> nothing {
    let logs = (kc $kubecfg -n data-proxy logs $pod -c nginx)
    let summaries = $logs | lines | where {|line| $line =~ '"event":"request"' }
    let fallback = $summaries | where {|line| $line =~ '"source":"bigquery"' }

    if ($fallback | is-empty) {
        error make {
            msg: 'the proxy never served a request from the fallback'
            label: {
                text: 'no fallback line'
                span: (metadata $fallback).span
            }
        }
    }
}

# Fail unless the concurrent burst stored exactly one entry.
def check-burst-stored [kubecfg: path, pod: string]: nothing -> nothing {
    let lines = kc $kubecfg -n data-proxy logs $pod -c nginx | lines
    let markers = (
        $lines
        | enumerate
        | where {|row| $row.item =~ '"uri":"/e2e"' }
        | get index
    )

    if ($markers | is-empty) {
        error make {
            msg: 'the burst marker request is missing from the proxy log'
            label: {
                text: 'marker search'
                span: (metadata $markers).span
            }
        }
    }

    let marker = $markers | last
    let stored = (
        $lines
        | enumerate
        | where {|row| $row.index > $marker }
        | where {|row| $row.item =~ '"uri":"/endpoint_participantes"' }
        | where {|row| $row.item =~ '"cache":"stored"' }
    )

    log info $"entries stored for the burst: ($stored | length)"

    if ($stored | length) != 1 {
        error make {
            msg: 'the concurrent burst did not store exactly one entry'
            label: {
                text: 'stored lines'
                span: (metadata $stored).span
            }
        }
    }
}

# Run the e2e test (triggers sync, seeds RLS, validates pipeline).
def "main k6 e2e" []: nothing -> string {
    let kubecfg = git-root | path join .kubeconfig

    let repo = git-root
    try { cd $repo } catch {|err|
        log error $'cd failed: ($err.msg)'
        return
    }

    docker build -q -t data-proxy:local -f Dockerfile . | ignore
    docker save -q data-proxy:local | mk $kubecfg image load - | ignore
    docker build -q -t data-proxy-nginx-proxy:local -f Dockerfile . | ignore
    docker save -q data-proxy-nginx-proxy:local | mk $kubecfg image load - | ignore

    (hm
        $kubecfg
        upgrade
        data-proxy
        $'($repo)/helm'
        --namespace
        data-proxy
        --values
        $'($repo)/scripts/values/data-proxy.yaml'
    ) | ignore

    clear-test-resources $kubecfg
    create-bucket $kubecfg

    (kc
        $kubecfg
        -n
        data-proxy
        create
        configmap
        data-proxy-e2e
        --from-file=e2e.ts=k6/e2e.ts
        --dry-run=client
        -o
        yaml
    ) | kc $kubecfg apply -f - | ignore

    apply-gcp-secret $kubecfg | ignore

    kc $kubecfg -n data-proxy delete testrun data-proxy-e2e --ignore-not-found | ignore

    let now = date now | date to-timezone utc
    let run_started: string = $now | format date '%Y-%m-%dT%H:%M:%SZ'
    kc $kubecfg apply -f k6/e2e.yaml | ignore

    wait-for-e2e $kubecfg

    print-e2e-logs $kubecfg

    let proxy_pod = (
        kc
            $kubecfg
            -n
            data-proxy
            get
            pods
            -l
            app.kubernetes.io/component=nginx-proxy
            -o
            'jsonpath={.items[0].metadata.name}'
        | str trim
    )

    check-fallback-served $kubecfg $proxy_pod

    let duckdb_pod = (
        kc
            $kubecfg
            -n
            data-proxy
            get
            pod
            -l
            app.kubernetes.io/name=data-proxy
            -l
            app.kubernetes.io/component=duckdb
            -o
            'jsonpath={.items[0].metadata.name}'
        | str trim
    )

    let hook_errors = (
        kc $kubecfg -n data-proxy logs $duckdb_pod $'--since-time=($run_started)'
        | lines
        | where {|line| $line =~ 'permission denied for schema rls' }
    )

    if ($hook_errors | is-not-empty) {
        error make {
            msg: 'anonymous traffic reached PostgREST, so the request hook failed'
            label: {
                text: 'hook errors'
                span: (metadata $hook_errors).span
            }
        }
    }
}

# Start Minikube and install the complete local stack.
def "main up" []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig

    let repo = git-root

    log info 'Starting Minikube…'
    start-minikube $kubecfg

    kc $kubecfg wait --for=condition=Ready nodes --all --timeout=5m

    log info 'Building container images…'
    build-images $kubecfg

    log info 'Building data-proxy:local…'
    docker build -t data-proxy:local -f ($repo | path join Dockerfile) $repo
    log info 'Loading data-proxy:local into Minikube…'
    docker save data-proxy:local | mk $kubecfg image load -

    log info 'Building Helm dependencies…'
    hm $kubecfg dependency build $'($repo)/helm'

    log info 'Installing platform charts…'
    install-platform $kubecfg

    log info 'Applying GCP secret…'
    apply-gcp-secret $kubecfg

    log info 'Installing data-proxy…'
    (hm
        $kubecfg
        install
        data-proxy
        $'($repo)/helm'
        --namespace
        data-proxy
        --values
        $'($repo)/scripts/values/data-proxy.yaml'
    )

    log info 'Waiting for deployments…'
    [
        keda/keda-operator
        keda/keda-operator-metrics-apiserver
        keda/keda-admission-webhooks
        k6-operator-system/k6-operator-controller-manager
        istio-system/istiod
        istio-ingress/istio-ingressgateway
        data-proxy/minio
        data-proxy/oidc
        data-proxy/data-proxy-nginx-proxy
        data-proxy/data-proxy-postgrest
        data-proxy/data-proxy-swagger-ui
    ] | wait-for deployment $kubecfg

    log info 'Waiting for statefulsets…'
    [
        data-proxy/data-proxy-duckdb
        data-proxy/data-proxy-valkey
    ] | wait-for statefulset $kubecfg

    show-status $kubecfg
}

# Remove the Minikube profile.
def "main down" []: nothing -> nothing {
    log info 'Deleting Minikube profile…'
    minikube --profile $PROFILE delete
}

# Print cluster status tables for pods, deployments, and scaled objects.
def show-status [kubecfg: path]: nothing -> nothing {
    print "
Pods:"

    print (kc $kubecfg -n data-proxy get pods -o json
        | try { from json } catch { {items: []} }
        | get items
        | each {|pod|
            let init = $pod.status.initContainerStatuses? | default []
            let containers = $pod.status.containerStatuses? | default []
            let all = ($init ++ $containers)
            let ready = $all | where $it.ready? | length
            let total = $all | length
            let restarts = $all | each { $in.restartCount? | default 0 } | math sum
            let age = $pod.metadata.creationTimestamp | into datetime | (date now) - $in

            {
                name: $pod.metadata.name,
                phase: $pod.status.phase,
                ready: $'($ready)/($total)',
                restarts: $restarts,
                age: (format-age $age),
            }
        }
        | sort-by name)

    print "
Deployments:"

    print (kc $kubecfg -n data-proxy get deploy -o json
        | try { from json } catch { {items: []} }
        | get items
        | each {|d|
            {
                name: $d.metadata.name,
                ready: ($d.status.readyReplicas? | default 0 | into int),
                replicas: ($d.status.replicas? | default 0 | into int),
            }
        }
        | sort-by name)

    print "
ScaledObjects:"

    try {
        print (kc $kubecfg -n data-proxy get scaledobject -o json
            | try { from json } catch { {items: []} }
            | get items
            | each {|s|
                let cond = $s.status.conditions? | default [] | last | default {}
                {
                    name: $s.metadata.name,
                    status: ($cond.type? | default '-'),
                    ready: ($cond.status? | default '-'),
                }
            }
            | sort-by name)
    } catch {
        log warning 'no scaledobjects found'
    }
}

# Script to create a testing environment with minikube
def main []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig

    mk $kubecfg status

    show-status $kubecfg
}
