use std/log
use lib.nu [
    chart-files
    checked
    fail
    IMAGES
    images-changed
    latest-tag
    make-dir
    newest-version
    next-dbos-version
    next-terraform-version
    parse-sha256sums
    provider-archive
    registry-documents
    read-data
    read-text
    release-commit
    release-tag
    replace-once
    sha256-file
    terraform-bump
    terraform-tag-version
    check-version
    valid-chart-version
    version-of-digest
    write-text
]

# CI platform configuration with GitHub Actions defaults.
let ci = {
    output_file: ($env.CI_OUTPUT_FILE? | default ($env.GITHUB_OUTPUT?))
    repository: ($env.CI_REPOSITORY? | default ($env.GITHUB_REPOSITORY?))
    registry: ($env.CI_REGISTRY? | default ghcr.io)
    registry_token: ($env.CI_REGISTRY_TOKEN? | default ($env.GITHUB_TOKEN?))
    registry_user: ($env.CI_REGISTRY_USER? | default ($env.GITHUB_ACTOR?))
    bot_name: ($env.CI_BOT_NAME? | default 'github-actions[bot]')
    bot_email: ($env.CI_BOT_EMAIL? | default 'github-actions[bot]@users.noreply.github.com')
}

# Return the image name from the environment, such as SYNC_IMAGE, or the default name of the image.
def image-name [image: record]: nothing -> string {
    $env | get --optional $'($image.id | str uppercase)_IMAGE' | default $image.name
}

# Return the repository owner derived from the repository path.
def ci-repository-owner []: nothing -> string {
    $ci.repository | parse '{owner}/{repo}' | first | get owner
}

# Return the value of an environment variable, or fail when it is not set or empty.
def required-env [name: string]: nothing -> string {
    let value = $env | get --optional $name

    if ($value | is-empty) {
        fail $'The environment variable ($name) is not set'
    }

    $value
}

# Return the latest Helm release tag of the repository, or null when there is none.
def previous-helm-tag []: nothing -> oneof<string, nothing> {
    git tag --sort=-v:refname | lines | latest-tag helm-v
}

# Write one CI output to the output file or print it locally.
def write-output [name: string, value: string]: nothing -> nothing {
    let line = $'($name)=($value)'

    if ($ci.output_file | is-empty) {
        print $line
    } else {
        $line + (char nl) | write-text --append $ci.output_file
    }
}

# Return the commit to compare with. A push to main compares with the last chart release, so a red run does not hide
# its files from the next green run. Another run compares with the commit before the push.
def comparison-base []: nothing -> string {
    let release = if ($env.GITHUB_REF? == refs/heads/main) { previous-helm-tag } else { null }

    if $release != null {
        git rev-list -n 1 $release | str trim
    } else {
        $env.BEFORE? | default HEAD~1
    }
}

# Detect changed components and write the build outputs.
def find-changes []: nothing -> nothing {
    log info 'Detecting changed components...'
    let force_full = ($env.FORCE_FULL? | default 'false') == 'true'
    let before = comparison-base
    let after = $env.GITHUB_SHA? | default HEAD

    let files = if $force_full {
        log info 'Force full build requested.'
        []
    } else {
        git diff --name-only $before $after | lines
    }

    let images = images-changed $files $force_full
    | each {|image|
        {
            name: $image.label
            containerfile: $image.containerfile
            tag_prefix: $image.tag_prefix
            image: $'($ci.registry)/($ci.repository)-($image.id)'
        }
    }

    let build_helm = $force_full or ($files | any {$in =~ ^helm/})

    if ($images | is-not-empty) { log info $'Detected ($images | length) image builds.' }
    if $build_helm { log info 'Helm chart build required.' }

    write-output build_helm ($build_helm | into string)
    write-output build_images (($images | is-not-empty) | into string)
    write-output build_matrix ({include: $images} | to json --raw)
}

# Resolve the tag of the image that the latest tag of the registry points to.
def resolve-image [image: record]: nothing -> string {
    let name = image-name $image
    let fallback = $image.fallback

    log info $'Resolving latest tag for image ($name)...'

    let repository = $'($ci.registry)/(ci-repository-owner)/($name)'
    let latest = checked 'crane digest' crane digest $'($repository):latest'
    let tag = crane ls $repository
    | lines
    | where $it =~ '^([0-9]+-)?[0-9a-f]{7}$'
    | par-each {|candidate| if (crane digest $'($repository):($candidate)' | str trim) == ($latest | str trim) { $candidate } }
    | compact
    | sort
    | get --optional 0

    if ($tag | is-not-empty) and $tag != '{}' {
        log info $'Resolved tag ($tag) for image ($name).'
        $tag
    } else if ($fallback | is-not-empty) {
        log warning $'No tag found for image ($name); using fallback ($fallback).'
        $fallback
    } else {
        fail $'No image tag is available for ($name)'
    }
}

# Resolve the latest image tags and write the resolved outputs.
def resolve-images []: nothing -> nothing {
    log info 'Resolving latest image tags from GHCR...'

    let resolved = $IMAGES | par-each {|image| {id: $image.id tag: (resolve-image $image)} }

    for image in $resolved {
        write-output $image.id $image.tag
    }

    log info 'Resolved all image tags.'
}

# Calculate and write the next Helm chart version from commit history. Return true when a release is due.
def read-next-version []: nothing -> bool {
    log info 'Calculating the next Helm chart version...'

    write-output current (read-data helm/Chart.yaml | get version)

    let tag = checked 'cog bump --dry-run' cog bump --auto --dry-run | release-tag

    if $tag == null {
        write-output release (false | into string)
        log info 'No release-level Conventional Commit found.'
        return false
    }

    let next = $tag | str replace helm-v ''

    write-output release (true | into string)
    write-output next $next
    write-output tag $tag

    log info $'Next Helm chart version: ($next)'

    true
}

# Pin image tags in the chart values file.
def pin-images []: nothing -> nothing {
    log info 'Pinning image tags in helm/values.yaml...'

    let replacements = $IMAGES
    | each {|image|
        {
            pattern: $image.values_pattern
            line: $'  image: ($ci.registry)/(ci-repository-owner)/(image-name $image):(required-env $image.sha_env)'
        }
    }

    let values = read-text helm/values.yaml
    | lines
    | each {|line|
        $replacements | reduce --fold $line {|replacement value|
            if $value =~ $replacement.pattern { $replacement.line } else { $value }
        }
    }
    | str join (char nl)
    | $in + (char nl)

    let pinned_lines = $values | lines
    let pinned = $replacements | where line in $pinned_lines | length

    if $pinned < ($IMAGES | length) {
        fail $'Expected ($IMAGES | length) pinned image lines, found ($pinned)'
    }

    $values | write-text helm/values.yaml
    log info $'Pinned ($pinned) image references in helm/values.yaml.'
}

# Set the chart version. The pre-bump hook of cog.toml calls it with the next version.
def bump-chart-version [
    version: string # The next chart version, without the helm-v prefix.
]: nothing -> nothing {
    log info $'Bumping Helm chart version to ($version)...'

    read-text helm/Chart.yaml
    | str replace --regex '(?m)^version: .*$' $'version: ($version)'
    | write-text helm/Chart.yaml

    log info 'Helm chart version updated.'
}

# Raise the DBOS application version when the code of the sync changed since the last release. The pre-bump hook of cog.toml calls it.
def bump-dbos-version []: nothing -> nothing {
    let tag = previous-helm-tag

    if $tag == null {
        fail 'No helm-v tag found, so the last release is unknown'
    }

    let diff = git diff --quiet $tag HEAD -- sync/src/sync | complete

    if $diff.exit_code > 1 {
        fail $'git diff failed: ($diff.stderr | str trim)'
    }

    let versions = read-text sync/src/sync/settings.py
    | parse --regex 'DBOS_APPLICATION_VERSION: str = "(?<version>[^"]+)"'

    if ($versions | is-empty) {
        fail 'sync/src/sync/settings.py has no DBOS_APPLICATION_VERSION line'
    }

    let current = $versions | first | get version
    let next = next-dbos-version $current ($diff.exit_code == 1)

    if $next == $current {
        log info $'The code of the sync did not change since ($tag). The application version stays ($current).'
        return
    }

    log info $'The code of the sync changed since ($tag). Raising the application version from ($current) to ($next).'

    replace-once helm/values.yaml '^\s+applicationVersion: ' $'    applicationVersion: ($next)'
    replace-once sync/src/sync/settings.py '^\s+DBOS_APPLICATION_VERSION: str = ' $'    DBOS_APPLICATION_VERSION: str = "($next)"'
    replace-once docs/operations/environment.md '^\| `DBOS_APPLICATION_VERSION` \|' $'| `DBOS_APPLICATION_VERSION` | `($next)` | DBOS application version. |'
}

# Log in to the OCI chart registry.
def login-chart-registry []: nothing -> nothing {
    log info $'Logging in to ($ci.registry)...'

    $ci.registry_token
    | checked 'helm registry login' helm registry login $ci.registry --username $ci.registry_user --password-stdin
    | ignore

    log info $'Logged in to ($ci.registry).'
}

# Package the Helm chart into the packaged directory.
def package-chart []: nothing -> nothing {
    log info 'Packaging Helm chart...'
    make-dir packaged
    helm package helm/ --destination packaged
    log info 'Helm chart packaged.'
}

# Bump the chart version, write the changelog, commit and tag with cog. The hooks of cog.toml pin the images first.
def tag-release []: nothing -> nothing {
    log info 'Bumping the Helm release with cog...'

    git config user.name $ci.bot_name
    git config user.email $ci.bot_email
    checked 'cog bump' cog bump --auto --skip-ci | print

    log info 'Helm release committed and tagged.'
}

# Write the changelog section of a release to packaged/notes.md, for the release page of any forge.
def write-release-notes [
    tag: string # The release tag, such as helm-v1.128.0.
]: nothing -> nothing {
    log info $'Writing the notes of ($tag)...'

    make-dir packaged
    checked 'cog changelog' cog changelog --at $tag | write-text packaged/notes.md
}

# Publish the packaged chart to the OCI registry.
def push-chart []: nothing -> nothing {
    let package = glob packaged/*.tgz | first

    log info $'Publishing ($package)...'

    checked 'helm push' helm push $package $'oci://($ci.registry)/(ci-repository-owner)/charts' | ignore

    log info 'Published the Helm chart.'
}

# Return the repository of the chart in the registry, such as ghcr.io/prefeitura-rio/charts/data-proxy.
def chart-repository []: nothing -> string {
    $'($ci.registry)/(ci-repository-owner)/charts/data-proxy'
}

# Log in to the registry for crane, when the run has a token.
def crane-login []: nothing -> nothing {
    if ($ci.registry_token | is-empty) {
        return
    }

    $ci.registry_token
    | checked 'crane auth login' crane auth login $ci.registry --username $ci.registry_user --password-stdin
    | ignore
}

# Return every tag of the chart repository with its digest.
def chart-tags [repository: string]: nothing -> list<record<tag: string, digest: string>> {
    crane ls $repository
    | lines
    | par-each {|tag| {tag: $tag, digest: (crane digest $'($repository):($tag)' | str trim)} }
}

# Return the version that a tag of the chart repository points to, or null when the tag does not exist.
def tag-version [tags: list<record<tag: string, digest: string>>, name: string]: nothing -> oneof<string, nothing> {
    let digest = $tags | where tag == $name | get --optional 0.digest

    if $digest == null { null } else { version-of-digest $tags $digest }
}

# Add a line to the summary of the job, or print it outside GitHub Actions.
def write-summary [line: string]: nothing -> nothing {
    if ($env.GITHUB_STEP_SUMMARY? | is-empty) {
        print $line
    } else {
        $line + (char nl) | write-text --append $env.GITHUB_STEP_SUMMARY
    }
}

# Find the version to promote and say whether production runs another one. It writes the outputs `version`, `current` and `pending`.
# A run that names a version in the variable VERSION promotes that version. Another run promotes the newest chart.
def find-promotion []: nothing -> nothing {
    crane-login

    let tags = chart-tags (chart-repository)
    let requested = $env.VERSION?
    let version = if ($requested | is-empty) { $tags | get tag | newest-version } else { $requested }

    if $version == null or not (valid-chart-version $version) {
        fail $'No chart version to promote: ($version)'
    }

    let current = tag-version $tags prod

    write-output version $version
    write-output current ($current | default none)
    write-output pending (($current != $version) | into string)

    log info $'Production runs ($current | default none). The chart to promote is ($version).'
    write-summary $'Production runs ($current | default none). The chart to promote is ($version).'
}

# Move the tag prod of the chart to a released version. Prod Flux follows that tag.
def move-prod-tag [
    version: string # The chart version, such as 2.13.0.
    --if-latest # Do nothing when a newer version exists, so an old approval does not undo a newer one.
]: nothing -> nothing {
    check-version $version

    crane-login

    let repository = chart-repository
    let tags = chart-tags $repository

    if ($tags | where tag == $version | is-empty) {
        fail $'The chart ($version) is not in ($repository)'
    }

    let newest = ($tags | get tag | newest-version)

    if $if_latest and $newest != $version {
        write-summary $'Skipped: ($version) is not the newest chart. The newest is ($newest).'

        return
    }

    let current = tag-version $tags prod | default none

    checked 'crane tag' crane tag $'($repository):($version)' prod | ignore

    write-summary $'prod: ($current) to ($version)'
}

# Return the commits since a revision, without the merge commits that cog ignores, with their subjects and files.
def commits-since [base: string]: nothing -> table<subject: string, files: list<string>> {
    git log --no-merges --format=%H $'($base)..HEAD'
    | lines
    | each {|sha|
        {
            subject: (git log -1 --format=%s $sha | str trim)
            files: (git diff-tree --no-commit-id --name-only -r $sha | lines)
        }
    }
}

# Decide whether a Helm release is needed: a chart file or an image path changed since the last release tag, and a
# release commit outside the Terraform provider and the docs came since then.
def check-release-needed [release: bool]: nothing -> nothing {
    log info 'Checking whether a Helm release is needed...'

    let base = previous-helm-tag | default HEAD~1
    let files = git diff --name-only $base HEAD | lines
    let changed = ($files | chart-files | is-not-empty) or (images-changed $files false | is-not-empty)
    let product = commits-since $base | any {|commit| release-commit $commit }
    let skip = (not $release) or (not $changed) or (not $product)

    write-output skip ($skip | into string)
    log info $'Release needed: (not $skip)'
}

# The provider type in the binary and in the archives, and the systems that the release builds for.
const PROVIDER = 'data-proxy'
const PROVIDER_TARGETS = [
    {os: linux, arch: amd64}
    {os: linux, arch: arm64}
    {os: darwin, arch: amd64}
    {os: darwin, arch: arm64}
    {os: windows, arch: amd64}
]

# Import the private key of the variable GPG_PRIVATE_KEY into a keyring under packaged/ and return its fingerprint.
# The command keeps GNUPGHOME for the next calls of gpg.
def --env import-signing-key []: nothing -> string {
    let home = $env.PWD | path join packaged gnupg

    make-dir $home
    chmod 700 $home
    $env.GNUPGHOME = $home

    required-env GPG_PRIVATE_KEY | checked 'gpg --import' gpg --batch --quiet --import | ignore

    gpg-output --with-colons --list-secret-keys
    | lines
    | parse --regex '^fpr:+(?<fingerprint>[0-9A-F]+):$'
    | first
    | get fingerprint
}

# Run gpg and return its output, or stop with its error.
def --wrapped gpg-output [...arguments: string]: nothing -> string {
    checked $'gpg ($arguments | first)' gpg --batch ...$arguments
}

# Run gh and return its output, or stop with its error.
def --wrapped gh-output [...arguments: string]: nothing -> string {
    checked $'gh ($arguments | first)' gh ...$arguments
}

# Sign a file with the imported key. The signature is the file `<file>.sig`, as the registry protocol wants.
def sign-file [file: path]: nothing -> nothing {
    let passphrase = if 'GPG_PASSPHRASE' in $env { $env.GPG_PASSPHRASE } else { '' }

    $passphrase
    | checked 'gpg --detach-sign' gpg --batch --yes --pinentry-mode loopback --passphrase-fd 0 --detach-sign --output $'($file).sig' $file
    | ignore
}

# Build the provider for one system, zip the binary into the output folder, and return the name of the archive. The
# binary reports the registry address from the registry variables, because it has no host name of its own.
def build-provider [target: record<os: string, arch: string>, version: string, output: path]: nothing -> string {
    let extension = if $target.os == windows { '.exe' } else { '' }
    let binary = $output | path join build $'($target.os)-($target.arch)' $'terraform-provider-($PROVIDER)_v($version)($extension)'
    let archive = provider-archive $PROVIDER $version $target.os $target.arch
    let address = $'(required-env TERRAFORM_REGISTRY_HOST)/(required-env TERRAFORM_REGISTRY_NAMESPACE)/($PROVIDER)'
    let flags = $'-s -w -X main.version=($version) -X main.address=($address)'

    make-dir ($binary | path dirname)
    log info $'Building ($archive)...'

    load-env {GOOS: $target.os, GOARCH: $target.arch, CGO_ENABLED: '0'}
    checked $'go build for ($target.os) ($target.arch)' go build -C terraform -trimpath -ldflags $flags -o $binary . | ignore
    checked 'zip' zip --quiet --junk-paths ($output | path join $archive) $binary | ignore

    $archive
}

# Decide whether a provider release is due and which version it has. The version comes from the variable VERSION when a
# person sets it, and else from the commits that changed terraform/ since the last tag terraform-v*. It writes the
# outputs `release` and `version`.
def find-provider-version []: nothing -> nothing {
    let requested = $env.VERSION?
    let last = git tag --sort=-v:refname | lines | latest-tag terraform-v
    let base = $last | default (git rev-list --max-parents=0 HEAD | lines | first)
    let bump = commits-since $base | terraform-bump
    let previous = if $last == null { null } else { $last | terraform-tag-version }

    let version = if ($requested | is-not-empty) {
        $requested
    } else if $bump != null {
        next-terraform-version $previous $bump
    }

    if $version != null { check-version $version }

    if $version == null {
        write-output release 'false'
        write-output version ''
    } else {
        write-output release 'true'
        write-output version $version
    }

    log info $'Last provider tag: ($last | default none). Bump: ($bump | default none). Next version: ($version | default none).'
}

# Build the Terraform provider for each system, sign the checksums and publish a GitHub Release with the archives.
# The tag is `terraform-v<version>`. The notes list the subjects of the commits of terraform/ since the last provider tag.
def build-provider-release [
    version: string # The provider version, such as 0.1.0.
]: nothing -> nothing {
    check-version $version

    import-signing-key | ignore

    let tag = $'terraform-v($version)'
    let previous = git tag --sort=-v:refname | lines | latest-tag terraform-v
    let range = if $previous == null { 'HEAD' } else { $'($previous)..HEAD' }
    let notes = checked 'git log' git log --no-merges --format='- %s' $range -- terraform
    let output = $env.PWD | path join packaged terraform

    let archives = $PROVIDER_TARGETS | each {|target| build-provider $target $version $output }

    let sums = $'terraform-provider-($PROVIDER)_($version)_SHA256SUMS'
    let lines = $archives | each {|archive| $'(sha256-file ($output | path join $archive))  ($archive)' }

    $lines | str join (char nl) | $in + (char nl) | write-text ($output | path join $sums)
    sign-file ($output | path join $sums)

    let files = [$sums $'($sums).sig' ...$archives] | each {|file| $output | path join $file }

    checked 'gh release create' gh release create $tag ...$files --target (git rev-parse HEAD | str trim) --title $tag --notes $notes | print
}

# Write the static registry of the provider from the published releases and upload it to the bucket of the registry. The
# variables TERRAFORM_REGISTRY_NAMESPACE and TERRAFORM_REGISTRY_BUCKET name the registry, and GITHUB_SERVER_URL and
# GITHUB_REPOSITORY name the downloads. The public key is the one of the imported private key.
def upload-registry []: nothing -> nothing {
    let fingerprint = import-signing-key
    let armor = gpg-output --armor --export $fingerprint
    let output = $env.PWD | path join packaged registry

    let versions = gh-output release list --limit 200 --json tagName --jq '.[].tagName'
    | lines
    | where $it =~ '^terraform-v\d+\.\d+\.\d+$'
    | each {|tag|
        let text = gh-output release download $tag --pattern '*_SHA256SUMS' --output -

        {version: ($tag | terraform-tag-version), sums: ($text | parse-sha256sums)}
    }

    let documents = registry-documents {
        namespace: (required-env TERRAFORM_REGISTRY_NAMESPACE)
        type: $PROVIDER
        base_url: $'(required-env GITHUB_SERVER_URL)/(required-env GITHUB_REPOSITORY)/releases/download'
        key: {id: ($fingerprint | str substring (-16)..), armor: $armor}
        targets: $PROVIDER_TARGETS
        versions: $versions
    }

    for document in $documents {
        let file = $output | path join $document.path

        make-dir ($file | path dirname)
        $document.body | to json | write-text $file
    }

    let bucket = required-env TERRAFORM_REGISTRY_BUCKET
    let sources = ['.well-known' v1] | each {|name| $output | path join $name }

    checked 'gcloud storage cp' gcloud storage cp --recursive --content-type=application/json --cache-control='public, max-age=60' ...$sources $'gs://($bucket)/' | print
}

# Find what changed and write the build outputs.
def 'main detect changes' []: nothing -> nothing {
    find-changes
}

# Plan the chart release: resolve the image tags, read the next version, and decide whether to skip. It writes the image ids,
# `current`, `release`, `next`, `tag` and `skip`. It changes no file.
def 'main plan chart' []: nothing -> nothing {
    resolve-images
    check-release-needed (read-next-version)
}

# Prepare the files of a chart release for cog: pin the images, set the chart version and raise the DBOS version.
# The pre-bump hook of cog.toml calls it with the next version.
def 'main prepare chart' [
    version: string # The next chart version, without the helm-v prefix.
]: nothing -> nothing {
    pin-images
    bump-chart-version $version
    bump-dbos-version
}

# Commit and tag the chart release with cog. The hook of cog.toml prepares the files first. The login comes first, so a bad
# token stops the job before the release commit and the tag exist.
def 'main release chart' []: nothing -> nothing {
    login-chart-registry
    tag-release
}

# Package the chart into the packaged directory.
def 'main package chart' []: nothing -> nothing {
    package-chart
}

# Publish the packaged chart and write the release notes. It logs in again, so a run at an old tag works alone.
def 'main publish chart' [
    tag: string # The release tag, such as helm-v1.128.0.
]: nothing -> nothing {
    login-chart-registry
    push-chart
    write-release-notes $tag
}

# Find the chart to promote and say whether production runs another one. It writes `version`, `current` and `pending`.
def 'main plan promotion' []: nothing -> nothing {
    find-promotion
}

# Move the tag prod to a released chart version.
def 'main promote chart' [
    version: string # The chart version, such as 2.13.0.
    --if-latest # Do nothing when a newer version exists, so an old approval does not undo a newer one.
]: nothing -> nothing {
    move-prod-tag $version --if-latest=$if_latest
}

# Decide whether a provider release is due and write `release` and `version`.
def 'main plan terraform' []: nothing -> nothing {
    find-provider-version
}

# Build, sign and publish the provider release.
def 'main release terraform' [
    version: string # The provider version, such as 0.1.0.
]: nothing -> nothing {
    build-provider-release $version
}

# Write the registry files of the provider and upload them.
def 'main publish terraform' []: nothing -> nothing {
    upload-registry
}

# Print the available CI subcommands.
def main []: nothing -> nothing {
    print 'Use a CI subcommand.'
}
