use std/log
use lib.nu [
    apply-gcp-secret
    build-local-images
    CLUSTER_NAME
    fail
    git-root
    local-image-tag
    local-kubeconfig
    make-dir
    poll
    remove-path
    wait-for
    wrap-helmfile
    wrap-kubectl
    write-text
]
use k6.nu *
use upgrade.nu *

const NETWORK = 'data-proxy'
const REGISTRY = 'registry'

# Display local cluster lifecycle commands.
def main []: nothing -> string {
    help main
}

# Start k3d and install the local platform stack.
def "main up" [
    --debug # Preserve failed k3d nodes and enable trace output.
]: nothing -> nothing {
    let kubecfg = local-kubeconfig
    let repo = git-root
    let duckdb = $repo | path join k3d .tmp duckdb

    if (podman network exists $NETWORK | complete).exit_code != 0 {
        log info $'Creating Podman network ($NETWORK)...'
        podman network create $NETWORK | ignore
    }

    let registries = k3d registry list --no-headers | lines
    if not ($registries | any {|line| $line | str starts-with $"k3d-($REGISTRY)" }) {
        log info $'Creating k3d registry ($REGISTRY)...'
        k3d registry create --default-network $NETWORK --port 5001 $REGISTRY
    }

    try {
        cd $repo
    } catch {|err|
        fail $'Cannot enter ($repo): ($err.msg)'
    }

    make-dir k3d/.tmp/duckdb
    chmod 777 k3d/.tmp/duckdb

    let clusters = k3d cluster list --no-headers | lines

    if not ($clusters | any {|line| $line | str starts-with $CLUSTER_NAME }) {
        log info 'Starting k3d...'
        let args = [
            cluster
            create
            --config
            k3d/k3d.yaml
            --volume
            $'($duckdb):/var/lib/data-proxy/duckdb@server:*;agent:*'
        ]
        | if $debug { append [--no-rollback --trace] } else { }
        k3d ...$args
    }

    k3d kubeconfig get $CLUSTER_NAME | write-text $kubecfg

    log info 'Waiting for the K3s metrics-server...'
    if not (poll {|| ((wrap-kubectl $kubecfg get --raw /apis/metrics.k8s.io/v1beta1/nodes | complete).exit_code == 0) } {
        interval: 5sec
        max_attempts: 60
    }) {
        fail "metrics-server didn't become ready within 5 minutes"
    }

    apply-gcp-secret $kubecfg --required

    let tag = build-local-images

    log info 'Updating Helmfile dependencies...'
    wrap-helmfile $kubecfg deps

    log info 'Syncing local manifests, platform charts, and Data Proxy with Helmfile...'
    wrap-helmfile $kubecfg --image-tag $tag sync
}

# Synchronize the local stack with the latest built images.
def "main sync" [
    --build # Build and push fresh local images before synchronizing.
]: nothing -> nothing {
    let kubecfg = local-kubeconfig

    apply-gcp-secret $kubecfg --required

    let tag = if $build { build-local-images } else { local-image-tag }

    log info $'Synchronizing Helmfile with local image tag ($tag)...'
    wrap-helmfile $kubecfg --image-tag $tag sync
}

# Remove the local k3d cluster.
def "main down" []: nothing -> nothing {
    log info 'Deleting k3d cluster...'
    k3d cluster delete $CLUSTER_NAME

    let kubecfg = local-kubeconfig
    let duckdb = git-root | path join k3d .tmp duckdb

    remove-path $kubecfg
    podman unshare rm --recursive --force $duckdb
}
