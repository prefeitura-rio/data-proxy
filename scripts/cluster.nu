use std/log

const PROFILE = "data-proxy"

# Path to the repository git-root.
def git-root []: nothing -> string {
    ^git rev-parse --show-toplevel | str trim
}

# Wrapped kubectl using the isolated kubeconfig.
def --wrapped kc [kubecfg: path, ...rest]: string -> string, nothing -> string {
    ^kubectl --kubeconfig=($kubecfg) ...$rest
}

# Wrapped minikube for the data-proxy profile with the isolated kubeconfig.
def --wrapped mk [kubecfg: path, ...rest]: nothing -> string {
    with-env { KUBECONFIG: $kubecfg } {
        ^minikube --profile $PROFILE ...$rest
    }
}

# Wait for the given resources to roll out.
def wait-for [kind: string, kubecfg: path, refs: list<string>]: nothing -> list<string> {
    $refs
    | each {|ref|
        let r = $ref | parse "{namespace}/{name}" | first
        kc $kubecfg -n $r.namespace rollout status $"($kind)/($r.name)" --timeout=15m
    }
}

# Install a Helm release from a record spec.
def helm [spec: record]: nothing -> string {
    let create_namespace = if ($spec.create_namespace? | default false) { [--create-namespace] } else { [] }

    let version = if $spec.version? != null { [--version $spec.version] } else { [] }

    let values = if $spec.values? != null { [--values $spec.values] } else { [] }

    let set = if $spec.set? != null { [--set $spec.set] } else { [] }

    let opts = [$create_namespace $version $values $set] | flatten

    let args = [install $spec.name $spec.chart --namespace $spec.namespace] ++ $opts

    ^helm ...$args
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
def build-images []: nothing -> string {
    let repo = git-root

    ^minikube image build -p $PROFILE -t data-proxy:local -f $"($repo)/Dockerfile" $repo

    ^minikube image build -p $PROFILE -t data-proxy-postgres:local -f $"($repo)/Dockerfile.postgres" $repo
}

# Install the platform Helm releases.
def install-platform []: nothing -> nothing {
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
    | each {|c| try { ^helm repo add ($c.chart | split row "/" | first) $c.repo } catch { null } }
    | ignore

    ^helm repo update

    $charts
    | each { helm $in }
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

# Start Minikube and install the complete local stack.
def "main up" []: nothing -> string {
    let kubecfg = git-root | path join ".kubeconfig"

    let repo = git-root

    log info "Starting Minikube…"
    start-minikube $kubecfg

    log info "Building container images…"
    build-images

    log info "Building Helm dependencies…"
    ^helm dependency build $"($repo)/helm"

    log info "Installing platform charts…"
    install-platform

    log info "Applying GCP secret…"
    apply-gcp-secret $kubecfg

    log info "Installing data-proxy…"
    ^helm install data-proxy $"($repo)/helm" --namespace data-proxy --values $"($repo)/scripts/values/data-proxy.yaml"

    log info "Waiting for deployments…"
    [
        keda/keda-operator
        keda/keda-operator-metrics-apiserver
        keda/keda-admission-webhooks
        k6-operator-system/k6-operator-controller-manager
        istio-system/istiod
        istio-ingress/istio-ingressgateway
        data-proxy/minio
        data-proxy/mock-oauth2-server
        data-proxy/webdis
        data-proxy/data-proxy-postgrest
        data-proxy/data-proxy-swagger-ui
    ] | wait-for deployment $kubecfg $in

    log info "Waiting for statefulsets…"
    [data-proxy/data-proxy-duckdb data-proxy/data-proxy-valkey] | wait-for statefulset $kubecfg $in

    kc $kubecfg get pods -A

    kc $kubecfg get scaledjobs -n data-proxy
}

# Create a one-time Job from the producer CronJob.
def "main sync" []: nothing -> string {
    let kubecfg = git-root | path join ".kubeconfig"

    let job_name = $"data-proxy-producer-manual-(date now | format date '%s')"

    log info "Creating manual sync job…"
    (kc
        $kubecfg
        -n
        data-proxy
        create
        job
        $job_name
        --from=cronjob/data-proxy-producer
    )

    log info "Waiting for job completion…"
    (kc
        $kubecfg
        -n
        data-proxy
        wait
        --for=condition=complete
        $"job/($job_name)"
        --timeout=2h
    )

    log info "Fetching job logs…"
    kc $kubecfg -n data-proxy logs $"job/($job_name)"
}

# Run a k6 load test.
def "main k6 load-test" [
    profile: string = "smoke"  # smoke, load, or stress
]: nothing -> string {
    let kubecfg = git-root | path join ".kubeconfig"

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

# Run the full sync pipeline and validate with k6.
def "main k6 e2e" []: nothing -> string {
    let kubecfg = git-root | path join ".kubeconfig"

    log info "Running sync pipeline…"
    main sync

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

    log info "Deleting previous e2e testrun…"
    kc $kubecfg -n data-proxy delete testrun data-proxy-e2e --ignore-not-found

    log info "Applying e2e testrun…"
    kc $kubecfg apply -f k6/e2e.yaml

    log info "Waiting for e2e completion…"
    (kc
        $kubecfg
        -n
        data-proxy
        wait
        --for=condition=complete
        testrun/data-proxy-e2e
        --timeout=15m
    )

    log info "Fetching e2e logs…"
    (kc
        $kubecfg
        -n
        data-proxy
        get
        pods
        -l
        k6-test-run-id=data-proxy-e2e
        -o
        jsonpath='{.items[0].metadata.name}'
    ) | kc $kubecfg -n data-proxy logs $in
}

# Remove the Minikube profile.
def "main down" []: nothing -> string {
    log info "Deleting Minikube profile…"
    ^minikube --profile $PROFILE delete
}

# Script to create a testing environment with minikube
def main []: nothing -> nothing {
    let kubecfg = git-root | path join ".kubeconfig"

    mk $kubecfg status

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
                age: ($age | into string),
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
