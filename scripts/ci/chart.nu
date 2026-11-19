use std/log
use ../lib.nu [checked fail IMAGES make-dir read-data read-text write-text]
use ../lib/release.nu [chart-files images-changed next-dbos-version release-commit release-tag replace-once]
use common.nu [ci-config ci-repository-owner commits-since image-name previous-helm-tag required-env write-output]

# Resolve the tag of the image that the latest tag of the registry points to.
def resolve-image [image: record]: nothing -> string {
    let name = image-name $image
    let fallback = $image.fallback

    log info $'Resolving latest tag for image ($name)...'

    let repository = $'((ci-config).registry)/(ci-repository-owner)/($name)'
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
            line: $'  image: ((ci-config).registry)/(ci-repository-owner)/(image-name $image):(required-env $image.sha_env)'
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
    checked 'docs.nu llms write' nu scripts/docs.nu llms write | ignore
}

# Log in to the OCI chart registry.
def login-chart-registry []: nothing -> nothing {
    log info $'Logging in to ((ci-config).registry)...'

    (ci-config).registry_token
    | checked 'helm registry login' helm registry login (ci-config).registry --username (ci-config).registry_user --password-stdin
    | ignore

    log info $'Logged in to ((ci-config).registry).'
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

    git config user.name (ci-config).bot_name
    git config user.email (ci-config).bot_email
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

    checked 'helm push' helm push $package $'oci://((ci-config).registry)/(ci-repository-owner)/charts' | ignore

    log info 'Published the Helm chart.'
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

# Plan the chart release: resolve the image tags, read the next version, and decide whether to skip. It writes the image ids,
# `current`, `release`, `next`, `tag` and `skip`. It changes no file.
export def 'main plan chart' []: nothing -> nothing {
    resolve-images
    check-release-needed (read-next-version)
}

# Prepare the files of a chart release for cog: pin the images, set the chart version and raise the DBOS version.
# The pre-bump hook of cog.toml calls it with the next version.
export def 'main prepare chart' [
    version: string # The next chart version, without the helm-v prefix.
]: nothing -> nothing {
    pin-images
    bump-chart-version $version
    bump-dbos-version
}

# Commit and tag the chart release with cog. The hook of cog.toml prepares the files first. The login comes first, so a bad
# token stops the job before the release commit and the tag exist.
export def 'main release chart' []: nothing -> nothing {
    login-chart-registry
    tag-release
}

# Package the chart into the packaged directory.
export def 'main package chart' []: nothing -> nothing {
    package-chart
}

# Publish the packaged chart and write the release notes. It logs in again, so a run at an old tag works alone.
export def 'main publish chart' [
    tag: string # The release tag, such as helm-v1.128.0.
]: nothing -> nothing {
    login-chart-registry
    push-chart
    write-release-notes $tag
}
