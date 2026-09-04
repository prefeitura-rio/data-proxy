use std/log

const PROFILE = "data-proxy"

# Path to the repository git-root.
def git-root []: nothing -> string {
    ^git rev-parse --show-toplevel | str trim
}

# Wrapped kubectl using the isolated kubeconfig.
def --wrapped kc [kubecfg: path, ...rest]: string -> string, nothing -> string {
    ^kubectl --kubeconfig=($kubecfg) --context=($PROFILE) ...$rest
}

# Wrapped minikube for the data-proxy profile with the isolated kubeconfig.
def --wrapped mk [kubecfg: path, ...rest]: nothing -> string {
    with-env { KUBECONFIG: $kubecfg } {
        ^minikube --profile $PROFILE ...$rest
    }
}

# Wrapped helm using the isolated kubeconfig.
def --wrapped hm [kubecfg: path, ...rest]: nothing -> string {
    with-env { KUBECONFIG: $kubecfg } {
        ^helm --kube-context $PROFILE ...$rest
    }
}

# Wait for the given resources to roll out.
def wait-for [kind: string, kubecfg: path, refs: list<string>]: nothing -> list<string> {
    $refs
    | each {|ref|
        let r = $ref | parse "{namespace}/{name}" | first
        log info $"  ($kind)/($r.namespace)/($r.name)…"
        kc $kubecfg -n $r.namespace rollout status $"($kind)/($r.name)" --timeout=15m
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
        (mk
            $kubecfg
            start
            --driver=podman
            --container-runtime=containerd
            --cpus=6
            --memory=12288
            --disk-size=40g
        )
    }

    mk $kubecfg update-context
}

# Build the data-proxy container images into Minikube.
def --env build-images [kubecfg: path]: nothing -> string {
    let repo = git-root
    cd $repo

    log info "Building data-proxy:local…"
    ^docker build -t data-proxy:local -f Dockerfile .
    log info "Loading data-proxy:local into Minikube…"
    ^docker save data-proxy:local | mk $kubecfg image load -

    log info "Building data-proxy-postgres:local…"
    ^docker build -t data-proxy-postgres:local -f Dockerfile.postgres .
    log info "Loading data-proxy-postgres:local into Minikube…"
    ^docker save data-proxy-postgres:local | mk $kubecfg image load -

    log info "Building localhost/k6:local…"
    ^docker build -t localhost/k6:local -f Dockerfile.k6 .
    log info "Loading localhost/k6:local into Minikube…"
    ^docker save localhost/k6:local | mk $kubecfg image load -

    log info "Building localhost/oidc:local…"
    ^docker build -t localhost/oidc:local -f Dockerfile.oidc .
    log info "Loading localhost/oidc:local into Minikube…"
    ^docker save localhost/oidc:local | mk $kubecfg image load -
}

# Install the platform Helm releases.
def install-platform [kubecfg: path]: nothing -> nothing {
    let charts = open scripts/charts.nuon
    | each {|c|
        if "values_file" in ($c | columns) {
            $c | reject values_file | merge { values: $"scripts/values/($c.values_file)" }
        } else { $c }
    }

    $charts
    | where {|c| "repo" in ($c | columns)}
    | select chart repo
    | uniq-by repo
    | each {|c| try { hm $kubecfg repo add ($c.chart | split row "/" | first) $c.repo } catch { null } }
    | ignore

    hm $kubecfg repo update

    $charts
    | each { helm $in $kubecfg }
    | ignore
}

# Apply the GCP service-account key as a Kubernetes secret.
def apply-gcp-secret [kubecfg: path]: nothing -> string {
    let creds = $env.HOME | path join ".config/gcloud/application_default_credentials.json"

    if not ($creds | path exists) {
        log warning "GCP credentials not found, skipping secret"
        return
    }

    (kc
        $kubecfg
        -n
        data-proxy
        create
        secret
        generic
        gcp-key
        $"--from-file=key.json=($creds)"
        --dry-run=client
        -o
        yaml
    ) | kc $kubecfg apply -f -
}

# Format a duration as a human-readable string, dropping sub-second precision.
def format-age [d: duration]: nothing -> string {
    let total_sec = $d / 1sec | into int
    let hr = ($total_sec // 3600)
    let min = (($total_sec mod 3600) // 60)
    let sec = (($total_sec mod 3600) mod 60)
    [
        [$hr "hr"]
        [$min "min"]
        [$sec "sec"]
    ]
    | each {|p| if $p.0 > 0 { $"($p.0)($p.1)" } }
    | flatten
    | str join " "
}

# Clear MinIO, Redis, and Postgres so the next k6 test starts from a clean baseline.
def clear-test-resources [kubecfg: path]: nothing -> nothing {
    log info "Clearing MinIO test-bucket…"
    let minio_pod = (
        (kc
            $kubecfg
            -n
            data-proxy
            get
            pod
            -l
            app.kubernetes.io/name=minio
            -o
            jsonpath='{.items[0].metadata.name}'
        )
        | str trim
    )
    let minio_user = (
        (kc
            $kubecfg
            -n
            data-proxy
            get
            secret
            minio
            -o
            jsonpath='{.data.root-user}'
        )
        | decode base64
        | decode utf-8
        | str trim
    )
    let minio_pass = (
        (kc
            $kubecfg
            -n
            data-proxy
            get
            secret
            minio
            -o
            jsonpath='{.data.root-password}'
        )
        | decode base64
        | decode utf-8
        | str trim
    )
    (kc
        $kubecfg
        -n
        data-proxy
        exec
        $minio_pod
        --
        sh
        -c
        $"mc alias set local http://localhost:9000 ($minio_user) ($minio_pass) >/dev/null 2>&1; mc rm --recursive --force local/test-bucket >/dev/null 2>&1; true"
    )

    log info "Clearing redis streams and consumer groups…"
    let valkey = (
        (kc
            $kubecfg
            -n
            data-proxy
            get
            pod
            -l
            app.kubernetes.io/name=valkey
            -o
            jsonpath='{.items[0].metadata.name}'
        )
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
        dp:prepare
        seeders
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
        dp:publish
        publishers
    )
    (kc
        $kubecfg
        -n
        data-proxy
        exec
        $valkey
        --
        sh
        -c
        "redis-cli --scan --pattern 'dp:*' | xargs -r redis-cli DEL"
    )

    log info "Clearing Postgres tables…"
    let duckdb = (
        (kc
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
            jsonpath='{.items[0].metadata.name}'
        )
        | str trim
    )
    let tables = (
        (kc
            $kubecfg
            -n
            data-proxy
            exec
            $duckdb
            --
            psql
            -U
            dataproxy
            -d
            dataproxy
            -t
            -A
            -c
            "SELECT tablename FROM pg_tables WHERE schemaname='pic' AND tablename NOT IN ('freshness','access_policy')"
        )
    )
    if ($tables | str trim | is-not-empty) {
        let drop_stmt = (
            $tables
            | lines
            | each {|t| $"DROP TABLE IF EXISTS pic.\"($t | str trim)\" CASCADE"}
            | str join "; "
        )
        (kc
            $kubecfg
            -n
            data-proxy
            exec
            $duckdb
            --
            psql
            -U
            dataproxy
            -d
            dataproxy
            -c
            $"($drop_stmt); DELETE FROM pic.freshness; DELETE FROM pic.access_policy;"
        )
    } else {
        (kc
            $kubecfg
            -n
            data-proxy
            exec
            $duckdb
            --
            psql
            -U
            dataproxy
            -d
            dataproxy
            -c
            "DELETE FROM pic.freshness; DELETE FROM pic.access_policy;"
        )
    }
}

# Run a k6 load test.
def "main k6 load-test" [
    profile: string = "smoke"  # smoke, load, or stress
]: nothing -> string {
    let kubecfg = git-root | path join ".kubeconfig"

    clear-test-resources $kubecfg

    log info "Creating k6 configmap…"
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
    | kc $kubecfg apply -f -

    log info "Deleting previous testrun…"
    kc $kubecfg -n data-proxy delete testrun data-proxy-load --ignore-not-found

    log info "Applying testrun…"
    open k6/run.yaml
    | update spec.runner.env {
        $in | each {|e| if $e.name == "K6_PROFILE" { $e | update value $profile } else { $e }}
    }
    | to yaml
    | kc $kubecfg apply -f -

    log info "Watching testrun…"
    kc $kubecfg -n data-proxy get testrun data-proxy-load -w
}

# Run the e2e test (triggers sync, seeds RLS, validates pipeline).
def "main k6 e2e" []: nothing -> string {
    let kubecfg = git-root | path join ".kubeconfig"

    clear-test-resources $kubecfg

    log info "Creating e2e configmap…"
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
    ) | kc $kubecfg apply -f -

    log info "Applying GCP secret…"
    apply-gcp-secret $kubecfg

    log info "Deleting previous e2e testrun…"
    kc $kubecfg -n data-proxy delete testrun data-proxy-e2e --ignore-not-found

    log info "Applying e2e testrun…"
    kc $kubecfg apply -f k6/e2e.yaml

    log info "Waiting for e2e completion…"
    while true {
        let jobs = (
            (kc
                $kubecfg
                -n
                data-proxy
                get
                jobs
                -l
                "k6_cr=data-proxy-e2e,runner=true"
                -o
                jsonpath='{.items}'
            )
            | str trim
        )
        if ($jobs | is-not-empty) and ($jobs != "[]") { break }
        sleep 1sec
    }

    while true {
        let phase = (
            (kc
                $kubecfg
                -n
                data-proxy
                get
                jobs
                -l
                "k6_cr=data-proxy-e2e,runner=true"
                -o
                jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Complete")].status}{.status.conditions[?(@.type=="Failed")].status}{end}'
            )
            | str trim
        )
        if ($phase | str contains "True") { break }
        sleep 2sec
    }

    log info "Fetching e2e logs…"
    (kc
        $kubecfg
        -n
        data-proxy
        get
        pods
        -l
        k6_cr=data-proxy-e2e,runner=true
        -o
        jsonpath='{.items[0].metadata.name}'
    ) | kc $kubecfg -n data-proxy logs $in
}

# Start Minikube and install the complete local stack.
def "main up" []: nothing -> nothing {
    let kubecfg = git-root | path join ".kubeconfig"

    let repo = git-root

    log info "Starting Minikube…"
    start-minikube $kubecfg

    kc $kubecfg wait --for=condition=Ready nodes --all --timeout=5m

    log info "Building container images…"
    build-images $kubecfg

    log info "Building Helm dependencies…"
    hm $kubecfg dependency build $"($repo)/helm"

    log info "Installing platform charts…"
    install-platform $kubecfg

    log info "Applying GCP secret…"
    apply-gcp-secret $kubecfg

    log info "Installing data-proxy…"
    (hm
        $kubecfg
        install
        data-proxy
        $"($repo)/helm"
        --namespace
        data-proxy
        --values
        $"($repo)/scripts/values/data-proxy.yaml"
    )

    log info "Waiting for deployments…"
    [
        keda/keda-operator
        keda/keda-operator-metrics-apiserver
        keda/keda-admission-webhooks
        k6-operator-system/k6-operator-controller-manager
        istio-system/istiod
        istio-ingress/istio-ingressgateway
        data-proxy/minio
        data-proxy/oidc
        data-proxy/webdis
        data-proxy/data-proxy-postgrest
        data-proxy/data-proxy-swagger-ui
    ] | wait-for deployment $kubecfg $in

    log info "Waiting for statefulsets…"
    [data-proxy/data-proxy-duckdb data-proxy/data-proxy-valkey] | wait-for statefulset $kubecfg $in

    show-status $kubecfg
}

# Remove the Minikube profile.
def "main down" []: nothing -> nothing {
    log info "Deleting Minikube profile…"
    ^minikube --profile $PROFILE delete
}

# Print cluster status tables for pods, deployments, and scaled jobs.
def show-status [kubecfg: path]: nothing -> nothing {
    print "\nPods:"

    print (kc $kubecfg -n data-proxy get pods -o json
        | from json
        | get items
        | each {|pod|
            let init = $pod.status.initContainerStatuses? | default []
            let containers = $pod.status.containerStatuses? | default []
            let all = ($init ++ $containers)
            let ready = $all | where $in.ready? == true | length
            let total = $all | length
            let restarts = $all | each { $in.restartCount? | default 0 } | math sum
            let age = $pod.metadata.creationTimestamp | into datetime | (date now) - $in

            {
                name: $pod.metadata.name,
                phase: $pod.status.phase,
                ready: $"($ready)/($total)",
                restarts: $restarts,
                age: (format-age $age),
            }
        }
        | sort-by name)

    print "\nDeployments:"

    print (kc $kubecfg -n data-proxy get deploy -o json
        | from json
        | get items
        | each {|d|
            {
                name: $d.metadata.name,
                ready: ($d.status.readyReplicas? | default 0 | into int),
                replicas: ($d.status.replicas? | default 0 | into int),
            }
        }
        | sort-by name)

    print "\nScaledJobs:"

    try {
        print (kc $kubecfg -n data-proxy get scaledjob -o json
            | from json
            | get items
            | each {|s|
                let cond = $s.status.conditions? | default [] | last | default {}
                {
                    name: $s.metadata.name,
                    status: ($cond.type? | default "-"),
                    ready: ($cond.status? | default "-"),
                }
            }
            | sort-by name)
    } catch { print --stderr "no scaledjobs found" }
}

# Script to create a testing environment with minikube
def main []: nothing -> nothing {
    let kubecfg = git-root | path join ".kubeconfig"

    mk $kubecfg status

    show-status $kubecfg
}
