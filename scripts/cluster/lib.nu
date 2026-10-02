use ../lib.nu [poll]

# Return the local Minikube profile name.
export def profile []: nothing -> string {
    'data-proxy'
}

# Return the data-proxy Kubernetes namespace.
export def namespace []: nothing -> string {
    'data-proxy'
}

# Return the isolated test schema name.
export def test-schema []: nothing -> string {
    'test'
}

# Return the repository git root.
export def git-root []: nothing -> string {
    git rev-parse --show-toplevel | str trim
}

# Run kubectl against the local Minikube profile.
export def --wrapped wrap-kubectl [kubecfg: path, ...rest: string]: string -> string, nothing -> string {
    with-env {
        KUBECONFIG: $kubecfg
    } {
        kubectl --context=(profile) ...$rest
    }
}

# Run Minikube against the local profile.
export def --wrapped wrap-minikube [kubecfg: path, ...rest: string]: nothing -> string {
    with-env {
        KUBECONFIG: $kubecfg
    } {
        minikube --profile (profile) ...$rest
    }
}

# Run Helm against the local Minikube profile.
export def --wrapped wrap-helm [kubecfg: path, ...rest: string]: nothing -> string {
    with-env {
        KUBECONFIG: $kubecfg
    } {
        ^helm --kube-context (profile) ...$rest
    }
}

# Wait for every listed rollout reference to become ready.
export def wait-for [kind: string, kubecfg: path]: list<string> -> nothing {
    for ref in $in {
        let target = $ref | parse '{namespace}/{name}' | first
        log info $'  ($kind)/($target.namespace)/($target.name)...'
        wrap-kubectl $kubecfg -n $target.namespace rollout status $'($kind)/($target.name)' --timeout=15m
    }
}

# Poll one readiness check until it passes or the configured attempts are exhausted.
export def wait-until [check: closure, config: record<interval: duration, max_attempts: int>]: nothing -> bool {
    poll $check $config
}
