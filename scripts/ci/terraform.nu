use std/log
use ../lib.nu [
    checked
    make-dir
    sha256-file
    write-text
]
use ../lib/release.nu [
    check-version
    latest-tag
    next-terraform-version
    parse-sha256sums
    provider-archive
    registry-documents
    terraform-bump
    terraform-tag-version
]
use common.nu [commits-since required-env write-output]

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

# Decide whether a provider release is due and write `release` and `version`.
export def 'main plan terraform' []: nothing -> nothing {
    find-provider-version
}

# Build, sign and publish the provider release.
export def 'main release terraform' [
    version: string # The provider version, such as 0.1.0.
]: nothing -> nothing {
    build-provider-release $version
}

# Write the registry files of the provider and upload them.
export def 'main publish terraform' []: nothing -> nothing {
    upload-registry
}
