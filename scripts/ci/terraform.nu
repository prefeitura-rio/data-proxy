use ../lib.nu [checked fail]
use ../lib/release.nu [parse-sha256sums provider-checksum provider-releases provider-version registry-documents release-notes]
use common.nu [required-env]

# Run GitHub CLI and return its output or stop with its error.
def --wrapped gh-output [...arguments: string]: nothing -> string {
    checked 'GitHub release query' gh ...$arguments
}

# Read every published provider release without a page or release-count limit.
def published-providers []: nothing -> table {
    gh-output api --paginate --slurp $'repos/(required-env GITHUB_REPOSITORY)/releases'
    | from json
    | flatten
    | provider-releases
}

# Reject invalid provider tags, unreviewed commits and non-increasing published versions.
@example "Validate a provider release tag" { nu --no-config-file scripts/ci.nu validate terraform v0.3.0 }
export def 'main validate terraform' [tag: string]: nothing -> nothing {
    let version = provider-version $tag
    if not ($tag | str starts-with v) or $version == null { fail 'Provider publication requires a stable vX.Y.Z tag' }

    let ancestor = git merge-base --is-ancestor $tag refs/remotes/origin/main | complete
    let head = git rev-parse HEAD | str trim
    let tagged = git rev-parse $'($tag)^{commit}' | str trim
    if $ancestor.exit_code != 0 or $head != $tagged { fail 'The provider tag must select the checked commit on main history' }

    for name in [TERRAFORM_REGISTRY_HOST TERRAFORM_REGISTRY_NAMESPACE TERRAFORM_REGISTRY_BUCKET] {
        required-env $name | ignore
    }
    let versions = published-providers | get version
    if ($versions | is-not-empty) and ($version in $versions or (($versions | append $version | sort --natural | last) != $version)) {
        fail 'The provider version must exceed every published provider version'
    }
}

# Import the signing key into an isolated keyring and export public signing metadata for later steps.
@example "Prepare provider signing and notes" { nu --no-config-file scripts/ci.nu prepare terraform v0.3.0 }
export def 'main prepare terraform' [tag: string]: nothing -> nothing {
    let home = $env.RUNNER_TEMP | path join data-proxy-provider-gnupg
    mkdir $home
    chmod 700 $home
    $env.GNUPGHOME = $home
    required-env GPG_PRIVATE_KEY | checked 'Import provider signing key' gpg --batch --quiet --import | ignore
    let fingerprint = checked 'Read signing key fingerprint' gpg --batch --with-colons --list-secret-keys
    | lines
    | parse --regex '^fpr:+(?<fingerprint>[0-9A-F]+):$'
    | first
    | get fingerprint

    mkdir packaged
    open --raw provider/CHANGELOG.md | release-notes $tag | save --force packaged/provider-notes.md
    [$'GNUPGHOME=($home)' $'GPG_FINGERPRINT=($fingerprint)']
    | str join (char nl)
    | $in + (char nl)
    | save --append $env.GITHUB_ENV
}

# Generate the provider registry from all published releases and upload its public documents.
@example "Publish provider registry metadata" { nu --no-config-file scripts/ci.nu publish terraform }
export def 'main publish terraform' []: nothing -> nothing {
    let fingerprint = required-env GPG_FINGERPRINT
    let armor = checked 'Export public provider signing key' gpg --batch --armor --export $fingerprint
    let releases = published-providers | each {|release|
        let assets = $release.assets | get name
        let checksum = $assets | provider-checksum
        let sums = gh-output release download $release.tag_name --pattern $checksum --output - | parse-sha256sums
        $release | update assets $assets | insert checksum_file $checksum | insert sums $sums
    }
    let documents = registry-documents {
        namespace: (required-env TERRAFORM_REGISTRY_NAMESPACE)
        type: data-proxy
        base_url: $'(required-env GITHUB_SERVER_URL)/(required-env GITHUB_REPOSITORY)/releases/download'
        key: {id: ($fingerprint | str substring (-16)..), armor: $armor}
        versions: $releases
    }
    let output = $env.PWD | path join packaged registry
    for document in $documents {
        let file = $output | path join $document.path
        mkdir ($file | path dirname)
        $document.body | to json | save --force $file
    }
    let sources = ['.well-known' v1] | each {|name| $output | path join $name }
    checked 'Publish static provider registry' gcloud storage cp --recursive --content-type=application/json --cache-control=public,max-age=60 ...$sources $'gs://(required-env TERRAFORM_REGISTRY_BUCKET)/' | print
}
