use std/log

export const IMAGE_REGISTRY = 'registry.localhost:5001'
export const IMAGE_TAG_FILE = 'k3d/.tmp/tag'
export const CLUSTER_NAME = 'data-proxy'
export const PROFILE = 'k3d-data-proxy'
export const NAMESPACE = 'data-proxy'
export const TEST_SCHEMA = 'test'

# The images of the project, one row for each. The CI and the local build read this table.
export const IMAGES = [
    {
        id: sync
        label: Pipeline
        name: data-proxy-sync
        containerfile: Containerfile.sync
        tag_prefix: ''
        local_tag_prefix: ''
        change_pattern: '^Containerfile\.sync$|^sync/(src/|pyproject\.toml$|uv\.lock$)|^console/migrations/'
        values_pattern: '^  image: .*data-proxy-sync:'
        sha_env: PIPELINE_SHA
        fallback: latest
    }
    {
        id: proxy
        label: Proxy
        name: data-proxy-proxy
        containerfile: Containerfile.proxy
        tag_prefix: ''
        local_tag_prefix: ''
        change_pattern: '^proxy/(src/|lux\.toml$|lux\.lock$)|^Containerfile\.proxy$'
        values_pattern: '^  image: .*data-proxy-proxy:'
        sha_env: PROXY_SHA
        fallback: latest
    }
    {
        id: jobs
        label: Jobs
        name: data-proxy-jobs
        containerfile: Containerfile.jobs
        tag_prefix: ''
        local_tag_prefix: ''
        change_pattern: '^Containerfile\.jobs$'
        values_pattern: '^\s+image: .*data-proxy-jobs:'
        sha_env: NU_SHA
        fallback: latest
    }
    {
        id: postgres
        label: PostgreSQL
        name: data-proxy-postgres
        containerfile: Containerfile.postgres
        tag_prefix: '17-'
        local_tag_prefix: '17.0.0-'
        change_pattern: '^Containerfile\.postgres$'
        values_pattern: '^  image: .*data-proxy-postgres:'
        sha_env: PG_SHA
        fallback: ''
    }
    {
        id: console
        label: Console
        name: data-proxy-console
        containerfile: Containerfile.console
        tag_prefix: ''
        local_tag_prefix: ''
        change_pattern: '^console/(ui|notebooks)/|^Containerfile\.console$'
        values_pattern: '^  image: .*data-proxy-console:'
        sha_env: CONSOLE_SHA
        fallback: latest
    }
]

# Log an error and stop the script with the same message.
export def fail [message: string]: nothing -> error {
    log error $message
    error make --unspanned {msg: $message}
}

# Return the output of a finished command, or fail with its error text.
export def check-result [what: string]: record<stdout: string, stderr: string, exit_code: int> -> string {
    let result = $in

    if $result.exit_code != 0 {
        fail $'($what) failed: ($result.stderr | str trim)'
    }

    $result.stdout
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
    let images = $in
    let context = $"/tmp/data-proxy-build-(random uuid)"

    try {
        mkdir $context

        # A tracked file that the working tree deleted is still in the index.
        for file in (git ls-files --cached --others --exclude-standard | lines | where ($it | path exists)) {
            let destination = $context | path join $file
            mkdir ($destination | path dirname)
            cp $file $destination
        }

        for image in $images {
            log info $'Building ($image.image)...'
            podman build --format docker --ignorefile ($context | path join .dockerignore) --tag $image.image --file $image.containerfile $context

            log info $'Pushing ($image.image)...'
            podman push --tls-verify=false $image.image
        }
    } catch {|err|
        fail $'Could not build local images: ($err.msg)'
    } finally {
        rm --recursive --force $context
    }
}

# Return the repository git root.
export def git-root []: nothing -> string {
    git rev-parse --show-toplevel | str trim
}

# Return the local k3d kubeconfig path.
export def local-kubeconfig []: nothing -> string {
    git-root | path join k3d .kubeconfig
}

# Read the immutable local image tag from the latest build.
export def local-image-tag []: nothing -> string {
    let tag_file = git-root | path join $IMAGE_TAG_FILE

    open --raw $tag_file | str trim
}

# Return local registry images for one immutable tag.
def local-images [tag: string]: nothing -> list<record<image: string, containerfile: string>> {
    $IMAGES | each {|image|
        {
            image: $'($IMAGE_REGISTRY)/($image.name):($image.local_tag_prefix)($tag)'
            containerfile: $image.containerfile
        }
    }
}

# Build and push the k6 image, which holds the scripts, with a tag of its own. Return the tag.
export def build-k6-image []: nothing -> string {
    let tag = $"k6-(date now | format date '%Y%m%d%H%M%S')"

    [
        {
            image: $"($IMAGE_REGISTRY)/k6:($tag)"
            containerfile: Containerfile.k6
        }
    ]
    | build-images

    $tag
}

# Build and push local images with one immutable tag.
export def build-local-images []: nothing -> string {
    let tag = $"local-(date now | format date '%Y%m%d%H%M%S')"
    let tag_file = git-root | path join $IMAGE_TAG_FILE

    log info $"Building local images with tag ($tag)..."
    local-images $tag | build-images

    $tag | save --force $tag_file

    $tag
}

# Apply the local GCP application-default-credentials Secret.
export def apply-gcp-secret [
    kubecfg: path
    --required # Fail when application-default credentials are absent.
]: nothing -> nothing {
    let credentials = $env.HOME | path join .config/gcloud/application_default_credentials.json

    if not ($credentials | path exists) {
        if $required {
            fail 'GCP application-default credentials are required. Run: gcloud auth application-default login'
        }

        log warning 'GCP credentials not found, skipping secret'
        return
    }

    log info 'Ensuring Kubernetes namespace for the GCP secret...'
    wrap-kubectl $kubecfg create namespace $NAMESPACE --dry-run=client -o yaml
    | wrap-kubectl $kubecfg apply -f -
    | ignore

    log info 'Applying GCP secret...'
    wrap-kubectl $kubecfg -n $NAMESPACE create secret generic gcp $'--from-file=key.json=($credentials)' --dry-run=client -o yaml
    | wrap-kubectl $kubecfg apply -f -
    | ignore

    let secret = wrap-kubectl $kubecfg -n $NAMESPACE get secret gcp -o json | from json

    if ($secret.data | get --optional 'key.json') == null {
        fail 'GCP secret was created without key.json'
    }

    log info 'GCP secret is ready'
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
        helmfile --file (git-root | path join k3d helmfile.yaml) ...$rest
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

# Return the first Helm release tag of a list that is newest first, or null when there is none.
export def latest-helm-tag []: list<string> -> oneof<string, nothing> {
    where $it =~ ^helm-v | get --optional 0
}
