use std/log

export const IMAGE_REGISTRY = 'registry.localhost:5001'
export const IMAGE_TAG_FILE = '.k3d/tag'
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
export def build-images []: list<record<image: string, containerfile: string>> -> nothing {
    for image in $in {
        log info $'Building ($image.image)...'
        podman build --tag $image.image --file $image.containerfile .

        log info $'Pushing ($image.image)...'
        podman push --tls-verify=false $image.image
    }
}

# Return the repository git root.
export def git-root []: nothing -> string {
    git rev-parse --show-toplevel | str trim
}

# Return the local k3d kubeconfig path.
export def local-kubeconfig []: nothing -> string {
    git-root | path join .kubeconfig
}

# Read the immutable local image tag from the latest build.
export def local-image-tag []: nothing -> string {
    let tag_file = git-root | path join $IMAGE_TAG_FILE

    try {
        open --raw $tag_file | str trim
    } catch {|err| fail $'Could not read local image tag: ($err.msg)' {
            command: local-image-tag
            span: (metadata $tag_file).span
        } }
}

# Return local registry images for one immutable tag.
def local-images [tag: string]: nothing -> list<record<image: string, containerfile: string>> {
    [
        {
            image: $"($IMAGE_REGISTRY)/data-proxy-sync:($tag)"
            containerfile: Containerfile.sync
        }
        {
            image: $"($IMAGE_REGISTRY)/data-proxy-postgres:17.0.0-($tag)"
            containerfile: Containerfile.postgres
        }
        {
            image: $"($IMAGE_REGISTRY)/data-proxy-proxy:($tag)"
            containerfile: Containerfile.proxy
        }
        {
            image: $"($IMAGE_REGISTRY)/data-proxy-jobs:($tag)"
            containerfile: Containerfile.jobs
        }
        {
            image: $"($IMAGE_REGISTRY)/k6:($tag)"
            containerfile: Containerfile.k6
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
    } catch {|err| fail $'Could not write local image tag: ($err.msg)' {
        command: build-local-images
        span: (metadata $tag_file).span
    } }

    $tag
}

# Apply the local GCP application-default-credentials Secret.
export def apply-gcp-secret [
    kubecfg: path
    --required # Fail when application-default credentials are absent.
]: nothing -> nothing {
    let credentials = $env.HOME | path join .config/gcloud/application_default_credentials.json

    if ($credentials | path exists) {
        log info 'Ensuring Kubernetes namespace for the GCP secret...'
        (wrap-kubectl
            $kubecfg
            create
            namespace
            $NAMESPACE
            --dry-run=client
            -o
            yaml
        )
        | wrap-kubectl $kubecfg apply -f -
        | ignore

        log info 'Applying GCP secret...'
        (wrap-kubectl
            $kubecfg
            -n
            $NAMESPACE
            create
            secret
            generic
            gcp
            $'--from-file=key.json=($credentials)'
            --dry-run=client
            -o
            yaml
        )
        | wrap-kubectl $kubecfg apply -f -
        | ignore

        let secret = try {
            wrap-kubectl $kubecfg -n $NAMESPACE get secret gcp -o json | from json
        } catch {|err| fail $'GCP secret was not created: ($err.msg)' {
                command: apply-gcp-secret
                span: (metadata $credentials).span
            } }

        if ($secret.data | get --optional 'key.json') == null {
            fail 'GCP secret was created without key.json' {
                command: apply-gcp-secret
                span: (metadata $credentials).span
            }
        }

        log info 'GCP secret is ready'
    } else if $required {
        fail 'GCP application-default credentials are required. Run: gcloud auth application-default login' {
            command: apply-gcp-secret
            span: (metadata $credentials).span
        }
    } else {
        log warning 'GCP credentials not found, skipping secret'
    }
}

# Run kubectl against the local k3d context.
export def --wrapped wrap-kubectl [kubecfg: path, ...rest: string]: string -> string, nothing -> string {
    with-env {
        KUBECONFIG: $kubecfg
    } {
        kubectl --context $PROFILE ...$rest
    }
}

# Run Helmfile against the local k3d kubeconfig and optional image tag.
export def --wrapped wrap-helmfile [kubecfg: path, --image-tag: string, ...rest: string]: nothing -> string, nothing -> nothing {
    let helm_env = {KUBECONFIG: $kubecfg}
    | if $image_tag == null { } else { upsert LOCAL_IMAGE_TAG $image_tag }

    with-env $helm_env {
        helmfile --file (git-root | path join helmfile.yaml) ...$rest
    }
}

# Wait for every listed rollout reference to become ready.
export def wait-for [kind: string, kubecfg: path]: list<string> -> nothing {
    for ref in $in {
        let target = $ref | parse '{namespace}/{name}' | first
        log info $'  ($kind)/($target.namespace)/($target.name)...'
        (wrap-kubectl
            $kubecfg
            -n
            $target.namespace
            rollout
            status
            $'($kind)/($target.name)'
            --timeout=15m
        )
    }
}
