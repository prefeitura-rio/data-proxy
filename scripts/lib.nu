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
        containerfile: Containerfile.pg
        tag_prefix: '18-'
        local_tag_prefix: '18.0.0-'
        change_pattern: '^Containerfile\.pg$'
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

# Log an error and stop the script with the same message. The label points at the message in the caller.
export def fail [message: string]: nothing -> error {
    log error $message

    error make {
        msg: $message
        labels: [
            {text: 'raised here', span: (metadata $message).span}
        ]
    }
}

# Return the text of a file, or fail with the reason.
export def read-text [file: path]: nothing -> string {
    try {
        open --raw $file | decode utf-8
    } catch {|err|
        fail $'Cannot read ($file): ($err.msg)'
    }
}

# Return the data of a YAML, TOML or JSON file, or fail with the reason.
export def read-data [file: path]: nothing -> record {
    try {
        open $file
    } catch {|err|
        fail $'Cannot read ($file): ($err.msg)'
    }
}

# Return the SHA-256 checksum of a file, as hexadecimal text, or fail with the reason.
export def sha256-file [file: path]: nothing -> string {
    try {
        open --raw $file | hash sha256
    } catch {|err|
        fail $'Cannot read ($file): ($err.msg)'
    }
}

# Save the text from the pipeline to a file, or fail with the reason. It replaces the file unless the flag is set.
export def write-text [file: path, --append]: string -> nothing {
    let text = $in

    try {
        if $append {
            $text | save --append $file
        } else {
            $text | save --force $file
        }
    } catch {|err|
        fail $'Cannot write ($file): ($err.msg)'
    }
}

# Parse JSON text from the pipeline, or fail with the reason.
export def parse-json []: string -> oneof<record, list<any>> {
    let text = $in

    try {
        $text | from json
    } catch {|err|
        fail $'Invalid JSON: ($err.msg)'
    }
}

# Parse TOML text from the pipeline, or fail with the reason.
export def parse-toml []: string -> record {
    let text = $in

    try {
        $text | from toml
    } catch {|err|
        fail $'Invalid TOML: ($err.msg)'
    }
}

# Parse YAML text from the pipeline, or fail with the reason.
export def parse-yaml []: string -> oneof<record, list<any>> {
    let text = $in

    try {
        $text | from yaml
    } catch {|err|
        fail $'Invalid YAML: ($err.msg)'
    }
}

# Create a folder with its parents, or fail with the reason.
export def make-dir [folder: path]: nothing -> nothing {
    try {
        mkdir $folder
    } catch {|err|
        fail $'Cannot create ($folder): ($err.msg)'
    }
}

# Remove a file or a folder, or fail with the reason. A path that is already gone is not an error.
export def remove-path [target: path]: nothing -> nothing {
    try {
        rm --recursive --force $target
    } catch {|err|
        fail $'Cannot remove ($target): ($err.msg)'
    }
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

# Build and push every input image record to its configured registry. A tracked file that the working tree deleted is
# still in the index, so the build context copies only the files that exist.
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

    read-text $tag_file | str trim
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

    $tag | write-text $tag_file

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

    let secret = wrap-kubectl $kubecfg -n $NAMESPACE get secret gcp -o json | parse-json

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

# Return the paths of a list that belong to the chart: the files of helm/ except its tests, and the values file.
export def chart-files []: list<string> -> list<string> {
    where (($it | str starts-with helm/) and ($it !~ ^helm/tests/)) or $it == helm/values.yaml
}

# Return the rows of IMAGES that a list of changed paths rebuilds, or every row when a full build is forced.
export def images-changed [files: list<string>, force_full: bool]: nothing -> list<record> {
    $IMAGES | where $force_full or ($files | any {$in =~ $it.change_pattern})
}

# Return true when a commit can release the chart: a `feat`, a `fix` or a type with `!`, as in cog.toml, that changes
# a file outside the Terraform provider, the docs and llms.txt. A provider or docs commit never releases the chart.
export def release-commit [commit: record<subject: string, files: list<string>>]: nothing -> bool {
    let level = $commit.subject =~ '^(feat|fix)(\([^)]*\))?!?: ' or $commit.subject =~ '^[a-z]+(\([^)]*\))?!: '
    let product = $commit.files | any {|file| $file !~ '^(terraform/|docs/|llms\.txt$)' }

    $level and $product
}

# Return true when a text is a full chart version such as 2.10.0. A range, a prefix or a channel tag is not one.
export def valid-chart-version [text: string]: nothing -> bool {
    $text =~ '^\d+\.\d+\.\d+$'
}

# Return the newest chart version of a list of tags, or null when none is a version. It compares the numbers.
export def newest-version []: list<string> -> oneof<string, nothing> {
    where (valid-chart-version $it)
    | sort-by {|tag| $tag | split row . | each {|part| $part | into int | fill --width 8 --alignment right --character 0 } | str join . }
    | last --strict 1
    | get --optional 0
}

# Return the version tag that has a digest, from rows of tag and digest, or null when none has it.
export def version-of-digest [tags: list<record<tag: string, digest: string>>, digest: string]: nothing -> oneof<string, nothing> {
    $tags
    | where (valid-chart-version $it.tag) and digest == $digest
    | get --optional 0.tag
}

# Return the first Helm release tag of a list that is newest first, or null when there is none.
export def latest-helm-tag []: list<string> -> oneof<string, nothing> {
    where $it =~ ^helm-v | get --optional 0
}

# Return the first provider release tag of a list that is newest first, or null when there is none.
export def latest-terraform-tag []: list<string> -> oneof<string, nothing> {
    where $it =~ ^terraform-v | get --optional 0
}

# Return the file name of a provider archive. The name follows the layout of a Terraform mirror.
export def provider-archive [name: string, version: string, os: string, arch: string]: nothing -> string {
    $'terraform-provider-($name)_($version)_($os)_($arch).zip'
}

# Return the version of a provider release tag, such as 0.1.3 for terraform-v0.1.3.
export def terraform-tag-version []: string -> string {
    str replace --regex '^terraform-v' ''
}

# Return the bump that one commit subject asks for: minor for a feature or a breaking change, patch for a fix.
def commit-bump [subject: string]: nothing -> oneof<string, nothing> {
    if $subject =~ '^feat(\([^)]*\))?: ' or $subject =~ '^[a-z]+(\([^)]*\))?!: ' {
        'minor'
    } else if $subject =~ '^fix(\([^)]*\))?: ' {
        'patch'
    }
}

# Return the bump that the commits of the provider ask for, or null when none needs a release. Only a commit that
# changes a file of terraform/ counts. The major version stays 0, so a breaking change raises the minor version.
export def terraform-bump []: list<record<subject: string, files: list<string>>> -> oneof<string, nothing> {
    let bumps = $in
    | where ($it.files | any {|file| $file =~ '^terraform/' })
    | each {|commit| commit-bump $commit.subject }
    | compact

    if 'minor' in $bumps {
        'minor'
    } else if 'patch' in $bumps {
        'patch'
    }
}

# Return the next provider version from the last one, or null for the first release, and a bump.
export def next-terraform-version [last: oneof<string, nothing>, bump: string]: nothing -> string {
    if $last == null {
        return '0.1.0'
    }

    let parts = $last
    | parse --regex '^(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)$'
    | first
    | into int major minor patch

    match $bump {
        minor => $'($parts.major).($parts.minor + 1).0'
        patch => $'($parts.major).($parts.minor).($parts.patch + 1)'
        _ => (fail $'The bump ($bump) is neither minor nor patch')
    }
}

# Return a record from the file name to the hash, from the text of a SHA256SUMS file.
export def parse-sha256sums []: string -> record {
    parse --regex '(?m)^(?<hash>[0-9a-f]+)\s+\*?(?<name>\S+)$'
    | reduce --fold {} {|row, sums| $sums | insert $row.name $row.hash }
}

# Return the files of a provider registry as a table of the path and the body. The registry is static: the service
# discovery, the versions of the provider and one download file for each version, system and architecture. The inputs
# are the namespace, the type, the base URL of the release downloads, the signing key (id and armor), the targets and
# the versions with the checksums of their archives. The documents hold no host name, because the host serves them.
export def registry-documents [inputs: record]: nothing -> table<path: string, body: record> {
    let root = $'v1/providers/($inputs.namespace)/($inputs.type)'
    let discovery = {
        path: '.well-known/terraform.json'
        body: {'providers.v1': '/v1/providers/'}
    }
    let versions = {
        path: $'($root)/versions'
        body: {
            versions: (
                $inputs.versions
                | each {|release|
                    {
                        version: $release.version
                        protocols: ['6.0']
                        platforms: $inputs.targets
                    }
                }
            )
        }
    }
    let downloads = $inputs.versions | each {|release|
        let base = $'($inputs.base_url)/terraform-v($release.version)'
        let sums = $'terraform-provider-($inputs.type)_($release.version)_SHA256SUMS'

        $inputs.targets | each {|target|
            let archive = provider-archive $inputs.type $release.version $target.os $target.arch
            let hashes = $release.sums | transpose name hash | where name == $archive

            if ($hashes | is-empty) {
                fail $'The checksums of ($release.version) have no ($archive)'
            }

            {
                path: $'($root)/($release.version)/download/($target.os)/($target.arch)'
                body: {
                    protocols: ['6.0']
                    os: $target.os
                    arch: $target.arch
                    filename: $archive
                    download_url: $'($base)/($archive)'
                    shasums_url: $'($base)/($sums)'
                    shasums_signature_url: $'($base)/($sums).sig'
                    shasum: ($hashes | first | get hash)
                    signing_keys: {
                        gpg_public_keys: [
                            {
                                key_id: $inputs.key.id
                                ascii_armor: $inputs.key.armor
                            }
                        ]
                    }
                }
            }
        }
    }
    | flatten

    [$discovery $versions] | append $downloads
}

# Return the tag that `cog bump --dry-run` prints when a release is due, or null when it prints a message.
export def release-tag []: string -> oneof<string, nothing> {
    lines | where $it =~ '^helm-v\d+\.\d+\.\d+' | get --optional 0
}
