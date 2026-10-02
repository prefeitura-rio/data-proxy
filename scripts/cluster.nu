use std/log
use ./lib.nu [fail poll]
use ./cluster/cleanup.nu [clear-test-resources]
use ./cluster/lib.nu [
    git-root
    wrap-helm
    wrap-kubectl
    wrap-minikube
    namespace
    profile
    wait-for
]
use ./cluster/images.nu [apply-gcp-secret build-images refresh-proxy start-minikube]
use ./cluster/k6.nu [run-modes run-perf run-suite]
use ./cluster/platform.nu [
    is-cnpg-healthy
    show-status
    verify-platform
    wait-for-control-plane
    wait-for-metrics
]

# Run the k6 smoke profile: one virtual user for 40 seconds.
def "main k6 smoke" [
    --ha # Run in HA mode, then return to single mode.
]: nothing -> nothing {
    run-perf (git-root | path join .kubeconfig) smoke $ha
}

# Run the standard k6 load profile.
def "main k6 load" [
    --ha # Run in HA mode, then return to single mode.
]: nothing -> nothing {
    run-perf (git-root | path join .kubeconfig) load $ha
}

# Run the k6 stress profile.
def "main k6 stress" [
    --ha # Run in HA mode, then return to single mode.
]: nothing -> nothing {
    run-perf (git-root | path join .kubeconfig) stress $ha
}

# Run E2E and deployment mode suites.
def "main k6 e2e" [
    --mode: string = full # Run e2e, modes, or full.
]: nothing -> nothing {
    if $mode not-in [e2e modes full] {
        fail $'--mode must be e2e, modes, or full: ($mode)' {
            command: k6-e2e
            span: (metadata $mode).span
        }
    }

    let kubecfg = git-root | path join .kubeconfig
    let repo = git-root

    try {
        cd $repo
    } catch {|err|
        log error $'Could not change to repository root: ($err.msg)'
        return
    }

    log info 'Building and loading local images...'
    build-images $kubecfg

    log info 'Waiting for the CNPG controller...'
    ['cnpg-system/cnpg-cloudnative-pg'] | wait-for deployment $kubecfg

    log info 'Applying GCP secret...'
    apply-gcp-secret $kubecfg

    log info 'Deleting init-db Jobs so they recreate PostgreSQL setup...'
    let old_init_jobs = wrap-kubectl $kubecfg -n (namespace) get jobs -l app.kubernetes.io/component=init-db -o name
    | lines
    if ($old_init_jobs | is-not-empty) {
        wrap-kubectl $kubecfg -n (namespace) delete ...$old_init_jobs --ignore-not-found
    }

    log info 'Upgrading data-proxy release...'
    wrap-helm $kubecfg upgrade data-proxy $'($repo)/helm' --namespace (namespace) --values $'($repo)/scripts/values/data-proxy.yaml'

    log info 'Restarting sync to load the rebuilt image...'
    wrap-kubectl $kubecfg -n (namespace) rollout restart deployment/data-proxy-sync
    wrap-kubectl $kubecfg -n (namespace) rollout status deployment/data-proxy-sync --timeout=180s

    log info 'Waiting for the init-db Job...'
    let init_jobs = wrap-kubectl $kubecfg -n (namespace) get jobs -l app.kubernetes.io/component=init-db -o name
    | lines

    for job in $init_jobs {
        wrap-kubectl $kubecfg -n (namespace) wait --for=condition=complete $job --timeout=360s
    }

    log info 'Waiting for data-proxy deployments...'
    [
        'data-proxy/data-proxy-proxy'
        'data-proxy/data-proxy-postgrest'
        'data-proxy/data-proxy-sync'
    ] | wait-for deployment $kubecfg

    log info 'Clearing test resources...'
    clear-test-resources $kubecfg

    match $mode {
        e2e => { run-suite $kubecfg e2e single }
        modes => { run-modes $kubecfg }
        _ => {
            run-suite $kubecfg full single
            run-modes $kubecfg
        }
    }
}

# Start Minikube and install the local platform stack.
def "main up" []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig
    let repo = git-root

    log info 'Starting Minikube...'
    start-minikube $kubecfg
    wrap-kubectl $kubecfg wait --for=condition=Ready nodes --all --timeout=5m

    log info 'Waiting for the Minikube control plane...'
    wait-for-control-plane $kubecfg

    log info 'Enabling metrics-server...'
    wrap-minikube $kubecfg addons enable metrics-server
    log info 'Waiting for metrics-server...'
    wait-for-metrics $kubecfg

    log info 'Building container images...'
    build-images $kubecfg

    log info 'Building Helm dependencies...'
    wrap-helm $kubecfg dependency build $'($repo)/helm'

    wrap-kubectl $kubecfg create namespace (namespace) --dry-run=client -o yaml | wrap-kubectl $kubecfg apply -f -
    log info 'Applying local Redis secret...'
    wrap-kubectl $kubecfg -n (namespace) create secret generic data-proxy-redis '--from-literal=REDIS_PASSWORD=valkey-local' '--from-literal=password=valkey-local' '--from-literal=REDIS_READ=redis://:valkey-local@data-proxy-valkey.data-proxy.svc.cluster.local:6379/1' '--from-literal=REDIS_WRITE=redis://:valkey-local@data-proxy-valkey-0.data-proxy-valkey-headless.data-proxy.svc.cluster.local:6379/0' '--from-literal=REDIS={"read":"redis://:valkey-local@data-proxy-valkey:6379/1","write":"redis://:valkey-local@data-proxy-valkey:6379/0"}' --dry-run=client -o yaml
    | wrap-kubectl $kubecfg apply -f -

    log info 'Installing platform charts with Helmfile...'
    with-env {
        KUBECONFIG: $kubecfg
    } {
        helmfile --concurrency 1 --file ($repo | path join helmfile.yaml) sync --wait --timeout 900
    }

    log info 'Verifying CNPG and KEDA stability...'
    verify-platform $kubecfg

    log info 'Applying GCP secret...'
    apply-gcp-secret $kubecfg

    log info 'Installing data-proxy...'
    wrap-helm $kubecfg upgrade --install data-proxy $'($repo)/helm' --namespace (namespace) --values $'($repo)/scripts/values/data-proxy.yaml'

    log info 'Waiting for data-proxy deployments...'
    ['data-proxy/data-proxy-swagger-ui'] | wait-for deployment $kubecfg

    log info 'Waiting for CNPG cluster...'
    if not (poll {|| is-cnpg-healthy $kubecfg } {
        interval: 2sec
        max_attempts: 300
    }) {
        fail "CNPG cluster didn't become healthy within 10 minutes" {
            command: main-up
            span: (metadata $kubecfg).span
        }
    }

    show-status $kubecfg
}

# Remove the local Minikube profile.
def "main down" []: nothing -> nothing {
    log info 'Deleting Minikube profile...'
    minikube --profile (profile) delete
}

# Print the local cluster status.
def main []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig
    wrap-minikube $kubecfg status
    show-status $kubecfg
}
