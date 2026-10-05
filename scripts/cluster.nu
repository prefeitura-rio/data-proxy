use std/log
use ./lib.nu [
    apply-gcp-secret
    build-local-images
    CLUSTER_NAME
    fail
    git-root
    local-kubeconfig
    poll
    wrap-helmfile
    wrap-kubectl
]
use ./k6.nu *

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
    let catalogs = $repo | path join .k3d catalogs

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
    } catch {|err| fail $'Could not change to repository root: ($err.msg)' {
            command: main-up
            span: (metadata $repo).span
        } }

    try {
        mkdir .k3d/catalogs/reader .k3d/catalogs/writer
        chmod 777 .k3d/catalogs/reader .k3d/catalogs/writer
    } catch {|err| fail $'Could not create k3d runtime directories: ($err.msg)' {
            command: main-up
            span: (metadata $repo).span
        } }

    let clusters = try {
        k3d cluster list --no-headers | lines
    } catch {|err| fail $'Could not list k3d clusters: ($err.msg)' {
            command: main-up
            span: (metadata $kubecfg).span
        } }

    if not ($clusters | any {|line| $line | str starts-with $CLUSTER_NAME }) {
        log info 'Starting k3d...'
        let args = [
            cluster
            create
            --config
            k3d.yaml
            --volume
            $'($catalogs)/reader:/var/lib/data-proxy/catalogs/reader@server:*;agent:*'
            --volume
            $'($catalogs)/writer:/var/lib/data-proxy/catalogs/writer@server:*;agent:*'
        ]
        | if $debug { append [--no-rollback --trace] } else { }
        k3d ...$args
    }

    try {
        k3d kubeconfig get $CLUSTER_NAME | save --force $kubecfg
    } catch {|err| fail $'Could not write k3d kubeconfig: ($err.msg)' {
            command: main-up
            span: (metadata $kubecfg).span
        } }

    log info 'Waiting for the K3s metrics-server...'
    if not (poll {|| ((wrap-kubectl $kubecfg get --raw /apis/metrics.k8s.io/v1beta1/nodes | complete).exit_code == 0) } {
        interval: 5sec
        max_attempts: 60
    }) {
        fail "metrics-server didn't become ready within 5 minutes" {
            command: main-up
            span: (metadata $kubecfg).span
        }
    }

    apply-gcp-secret $kubecfg --required

    let tag = build-local-images

    log info 'Updating Helmfile dependencies...'
    wrap-helmfile $kubecfg deps

    log info 'Syncing local manifests, platform charts, and Data Proxy with Helmfile...'
    wrap-helmfile $kubecfg --image-tag $tag sync --state-values-set ha.enabled=false
}

# Remove the local k3d cluster.
def "main down" []: nothing -> nothing {
    log info 'Deleting k3d cluster...'
    k3d cluster delete $CLUSTER_NAME

    let kubecfg = local-kubeconfig

    try {
        rm --force $kubecfg
    } catch {|err| fail $'Could not remove k3d kubeconfig: ($err.msg)' {
            command: main-down
            span: (metadata $kubecfg).span
        } }
}
