use std/log

export const IMAGE_REGISTRY = 'registry.localhost:5001'
export const IMAGE_TAG_FILE = 'k3d/.tmp/tag'
export const CLUSTER_NAME = 'data-proxy'
export const PROFILE = 'k3d-data-proxy'
export const NAMESPACE = 'data-proxy'
export const TEST_SCHEMA = 'default__test'

# Project images shared by CI and local builds.
export const IMAGES = [
    {
        id: sync
        label: Sync
        name: data-proxy-sync
        containerfile: Containerfile.sync
        tag_prefix: ''
        local_tag_prefix: ''
        change_pattern: '^Containerfile\.sync$|^sync/(src/|pyproject\.toml$|uv\.lock$)|^migrations/'
        values_pattern: '^  image: .*data-proxy-sync:'
        sha_env: SYNC_SHA
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
        tag_prefix: '18-'
        local_tag_prefix: '18.0.0-'
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

# Log an error and stop the script with contextual failure details.
@example "Stop with an error" { fail 'Example failure' }
export def fail [message: string]: nothing -> error {
    log error $message

    error make {
        msg: $message
        labels: [
            {text: 'raised here', span: (metadata $message).span}
        ]
    }
}

# Return the output of a finished command, or fail with its error text.
@example "Read successful command output" { {stdout: 'ready', stderr: '', exit_code: 0} | check-result 'probe' } --result 'ready'
export def check-result [what: string]: record<stdout: string, stderr: string, exit_code: int> -> string {
    let result = $in

    if $result.exit_code != 0 {
        fail $'($what) failed: ($result.stderr | str trim)'
    }

    $result.stdout
}

# Run an external command with the input of the pipeline, and return its output or fail with its error text and name.
@example "Capture command output" { checked 'Nushell version' nu --version }
export def --wrapped checked [what: string, ...command: string]: nothing -> string, string -> string {
    let input = $in
    let arguments = $command | skip 1

    $input | ^($command | first) ...$arguments | complete | check-result $what
}

# Poll a readiness check until it returns true or attempts are exhausted.
@example "Check immediate readiness" { poll {|| true } {interval: 1ms, max_attempts: 1} } --result true
export def poll [check: closure, config: record<interval: duration, max_attempts: int>]: nothing -> bool {
    mut attempts = $config.max_attempts

    while $attempts > 0 {
        if (do $check) { return true }
        sleep $config.interval
        $attempts -= 1
    }

    false
}

# Build and publish configured images using existing files from the working tree.
@example "Build and publish an image" { [{image: 'localhost:5000/data-proxy-sync:example', containerfile: 'Containerfile.sync'}] | build-images }
export def build-images []: list<record<image: string, containerfile: string>> -> nothing {
    let images = $in
    let context = $"/tmp/data-proxy-build-(random uuid)"

    try {
        mkdir $context

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
@example "Locate the checkout" { git-root }
export def git-root []: nothing -> string {
    git rev-parse --show-toplevel | str trim
}

# Return the local k3d kubeconfig path.
@example "Locate the cluster kubeconfig" { local-kubeconfig }
export def local-kubeconfig []: nothing -> string {
    git-root | path join k3d .kubeconfig
}

# Read the immutable local image tag from the latest build.
@example "Read the latest local build tag" { local-image-tag }
export def local-image-tag []: nothing -> string {
    let tag_file = git-root | path join $IMAGE_TAG_FILE

    (open --raw $tag_file | decode utf-8) | str trim
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

# Build and publish the k6 image with its own tag and return the tag.
@example "Build and publish a k6 image" { build-k6-image }
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
@example "Build and publish local images" { build-local-images }
export def build-local-images []: nothing -> string {
    let tag = $"local-(date now | format date '%Y%m%d%H%M%S')"
    let tag_file = git-root | path join $IMAGE_TAG_FILE

    log info $"Building local images with tag ($tag)..."
    local-images $tag | build-images

    $tag | save --force $tag_file

    $tag
}

# Return a credential validation error or nothing when credentials work or cannot be checked.
@example "Skip a service-account credential check" { gcp-credentials-problem {type: service_account} {|| {} } } --result null
export def gcp-credentials-problem [credentials: record, exchange: closure]: nothing -> oneof<string, nothing> {
    if $credentials.type? != 'authorized_user' {
        return null
    }

    let form = {
        client_id: $credentials.client_id
        client_secret: $credentials.client_secret
        refresh_token: $credentials.refresh_token
        grant_type: refresh_token
    }
    let answer = do $exchange $form

    if ($answer.error? in [invalid_grant invalid_rapt]) {
        'GCP application-default credentials expired. Run: gcloud auth application-default login'
    }
}

# Apply the local GCP application-default-credentials Secret.
@example "Install local cluster credentials" { apply-gcp-secret '.kube/config' --required }
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

    let problem = gcp-credentials-problem ((open --raw $credentials | decode utf-8) | from json) {|form|
        try { http post --allow-errors --content-type application/x-www-form-urlencoded https://oauth2.googleapis.com/token $form } catch { {} }
    }

    if $problem != null {
        fail $problem
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
@example "List local sync pods" { wrap-kubectl '.kube/config' -n data-proxy get pods }
export def --wrapped wrap-kubectl [kubecfg: path, ...rest: string]: string -> string, nothing -> string {
    with-env {
        KUBECONFIG: $kubecfg
    } {
        kubectl --context $PROFILE ...$rest
    }
}

# Run Helmfile against the local k3d kubeconfig and optional image tag.
@example "Preview the local deployment" { wrap-helmfile '.kube/config' diff }
export def --wrapped wrap-helmfile [kubecfg: path, --image-tag: string, ...rest: string]: nothing -> string, nothing -> nothing {
    let helm_env = {KUBECONFIG: $kubecfg}
    | if $image_tag == null { } else { upsert LOCAL_IMAGE_TAG $image_tag }

    with-env $helm_env {
        helmfile --file (git-root | path join k3d helmfile.yaml) ...$rest
    }
}

# Wait for every listed rollout reference to become ready.
@example "Wait for a sync rollout" { ['data-proxy/data-proxy-sync'] | wait-for deployment '.kube/config' }
export def wait-for [kind: string, kubecfg: path]: list<string> -> nothing {
    for ref in $in {
        let target = $ref | parse '{namespace}/{name}' | first

        log info $'  ($kind)/($target.namespace)/($target.name)...'
        wrap-kubectl $kubecfg -n $target.namespace rollout status $'($kind)/($target.name)' --timeout=15m
    }
}

# Render one SQL template with minijinja-cli and a JSON context, and return the SQL.
@example "Render a test SQL template" { render-template 'sync/tests/templates/postgres/create_ducklake_catalog_secret.sql' {host: localhost, port: 5432, database: ducklake, user: test, password: test} }
export def render-template [template: path, context: record = {}]: nothing -> string {
    let file = mktemp --tmpdir --suffix .json

    $context | to json | save --force $file
    let result = minijinja-cli --strict --autoescape none --format json $template $file | complete
    rm --recursive --force $file

    $result | check-result $'Rendering of ($template)'
}

# Return the address of one database of the PostgreSQL test server.
@example "Construct a test connection URL" { test-url test '5432' } --result 'postgresql://test:test@127.0.0.1:5432/test'
export def test-url [database: string, port: string]: nothing -> string {
    $'postgresql://test:test@127.0.0.1:($port)/($database)'
}

# Run one SQL script on the database at the address, and stop at the first error.
@example "Probe an example database" { 'SELECT 1;' | run-sql 'postgresql://localhost/example' }
export def run-sql [url: string]: string -> nothing {
    psql $url --no-psqlrc --quiet --set=ON_ERROR_STOP=1
    | complete
    | check-result $'psql on ($url)'
    | ignore
}

# Default chart roles for migration grants in tests.
export const MIGRATION_ROLES = {
    AUTH_ANON_ROLE: anon
    AUTH_USER_ROLE: dp_api
    AUTH_SIGNER_ROLE: migration_signer
    PGUSER: test
}

# Apply migrations to a database, optionally stopping at --to.
@example "Apply application migrations" { goose-up console 'postgresql://localhost/example' }
export def goose-up [folder: path, url: string, --to: int, --roles: record = {}]: nothing -> nothing {
    let dir = git-root | path join migrations $folder
    let command = if $to == null { [up] } else { [up-to ($to | into string)] }

    with-env ($MIGRATION_ROLES | merge $roles) {
        goose -dir $dir postgres $url ...$command
    }
    | complete
    | check-result $'goose ($command | str join " ") of ($folder)'
    | ignore
}

# Create the DBOS tables of the database at the address, which the console views read.
@example "Apply DBOS migrations" { dbos-migrate 'postgresql://localhost/example' }
export def dbos-migrate [url: string]: nothing -> nothing {
    uv run --frozen --project (git-root | path join sync) dbos migrate --sys-db-url $url --schema dbos
    | complete
    | check-result $'dbos migrate on ($url)'
    | ignore
}
