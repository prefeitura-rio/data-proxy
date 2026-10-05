use std/log

export const IMAGE_REGISTRY = 'registry.localhost:5001'
export const IMAGE_TAG_FILE = '.k3s/tag'
export const CLUSTER_NAME = 'data-proxy'
export const PROFILE = 'k3d-data-proxy'
export const NAMESPACE = 'data-proxy'
export const TEST_SCHEMA = 'test'

# Log an error and raise a labeled error in one call.
export def fail [message: string, context: record<command: string, span: record>]: nothing -> error {
    log error $message
    error make {
        msg: $message
        label: {text: $context.command, span: $context.span}
    }
}

# Poll a readiness check until it returns true or attempts are exhausted.
export def poll [check: closure, config: record<interval: duration, max_attempts: int>]: nothing -> bool {
    mut attempts = $config.max_attempts

    while $attempts > 0 {
        if (do $check) { return true }
        sleep $config.interval
        $attempts -= 1
    }

    false
}

# Build and push every input image record to its configured registry.
export def build-images []: list<record<image: string, dockerfile: string>> -> nothing {
    for image in $in {
        log info $'Building ($image.image)...'
        podman build --tag $image.image --file $image.dockerfile .

        log info $'Pushing ($image.image)...'
        podman push --tls-verify=false $image.image
    }
}

# Return the repository git root.
export def git-root []: nothing -> string {
    git rev-parse --show-toplevel | str trim
}

# Return the local k3d kubeconfig path.
export def local-kubeconfig []: nothing -> path {
    git-root | path join .kubeconfig
}

# Return local registry images for one immutable tag.
def local-images [tag: string]: nothing -> list<record<image: string, dockerfile: string>> {
    [
        {
            image: $"($IMAGE_REGISTRY)/data-proxy-sync:($tag)"
            dockerfile: Dockerfile.sync
        }
        {
            image: $"($IMAGE_REGISTRY)/data-proxy-postgres:17.0.0-($tag)"
            dockerfile: Dockerfile.postgres
        }
        {
            image: $"($IMAGE_REGISTRY)/data-proxy-proxy:($tag)"
            dockerfile: Dockerfile.proxy
        }
        {
            image: $"($IMAGE_REGISTRY)/data-proxy-jobs:($tag)"
            dockerfile: Dockerfile.jobs
        }
        {
            image: $"($IMAGE_REGISTRY)/k6:($tag)"
            dockerfile: Dockerfile.k6
        }
    ]
}

# Build and push local images with one immutable tag.
export def build-local-images []: nothing -> string {
    let tag = $"local-(date now | format date '%Y%m%d%H%M%S')"
    let tag_file = git-root | path join $IMAGE_TAG_FILE

    log info $"Building local images with tag ($tag)..."
    local-images $tag | build-images

    try {
        $tag | save --force $tag_file
    } catch {|err|
        fail $'Could not write local image tag: ($err.msg)' {
            command: build-local-images
            span: (metadata $tag_file).span
        }
    }

    $tag
}

# Apply the optional local GCP application-default-credentials Secret.
export def apply-gcp-secret [kubecfg: path]: nothing -> nothing {
    let credentials = $env.HOME | path join .config/gcloud/application_default_credentials.json

    if ($credentials | path exists) {
        log info 'Applying GCP secret...'
        wrap-kubectl $kubecfg -n $NAMESPACE create secret generic gcp-key $'--from-file=key.json=($credentials)' --dry-run=client -o yaml
        | wrap-kubectl $kubecfg apply -f -
        | ignore
    } else {
        log warning 'GCP credentials not found, skipping secret'
    }
}

# Run kubectl against the local k3d context.
export def --wrapped wrap-kubectl [kubecfg: path, ...rest: string]: string -> string, nothing -> string {
    with-env {
        KUBECONFIG: $kubecfg
    } {
        kubectl --context=$PROFILE ...$rest
    }
}

# Synchronize the Data Proxy release with the requested image tag and mode.
export def sync-data-proxy [kubecfg: path, image_tag: string, ha: bool]: nothing -> nothing {
    with-env {
        KUBECONFIG: $kubecfg
        LOCAL_IMAGE_TAG: $image_tag
    } {
        helmfile --file (git-root | path join helmfile.yaml) sync --selector name=data-proxy --state-values-set $'ha.enabled=($ha)'
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
