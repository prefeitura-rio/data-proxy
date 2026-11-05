use std/log
use lib.nu [
    chart-files
    check-result
    fail
    IMAGES
    images-changed
    latest-helm-tag
    make-dir
    newest-version
    read-data
    read-text
    release-commit
    release-tag
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
    git tag --sort=-v:refname | lines | latest-helm-tag
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
def 'main changes' []: nothing -> nothing {
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
    let latest = crane digest $'($repository):latest' | complete | check-result 'crane digest'
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
def 'main images resolve' []: nothing -> nothing {
    log info 'Resolving latest image tags from GHCR...'

    let resolved = $IMAGES | par-each {|image| {id: $image.id tag: (resolve-image $image)} }

    for image in $resolved {
        write-output $image.id $image.tag
    }

    log info 'Resolved all image tags.'
}

# Calculate and write the next Helm chart version from commit history.
def 'main version' []: nothing -> nothing {
    log info 'Calculating the next Helm chart version...'

    write-output current (read-data helm/Chart.yaml | get version)

    let tag = cog bump --auto --dry-run | complete | check-result 'cog bump --dry-run' | release-tag

    if $tag == null {
        write-output release (false | into string)
        log info 'No release-level Conventional Commit found.'
        return
    }

    let next = $tag | str replace helm-v ''

    write-output release (true | into string)
    write-output next $next
    write-output tag $tag

    log info $'Next Helm chart version: ($next)'
}

# Pin image tags in the chart values file.
def 'main images pin' []: nothing -> nothing {
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
def 'main chart bump' [
    version: string # The next chart version, without the helm-v prefix.
]: nothing -> nothing {
    log info $'Bumping Helm chart version to ($version)...'

    read-text helm/Chart.yaml
    | str replace --regex '(?m)^version: .*$' $'version: ($version)'
    | write-text helm/Chart.yaml

    log info 'Helm chart version updated.'
}

# Update the checkout from main.
def 'main git update' []: nothing -> nothing {
    log info 'Updating checkout from main...'
    git pull --rebase origin main
    log info 'Checkout updated from main.'
}

# Log in to the OCI chart registry.
def 'main chart login' []: nothing -> nothing {
    log info $'Logging in to ($ci.registry)...'

    $ci.registry_token
    | helm registry login $ci.registry --username $ci.registry_user --password-stdin
    | complete
    | check-result 'helm registry login'
    | ignore

    log info $'Logged in to ($ci.registry).'
}

# Package the Helm chart into the packaged directory.
def 'main chart package' []: nothing -> nothing {
    log info 'Packaging Helm chart...'
    make-dir packaged
    helm package helm/ --destination packaged
    log info 'Helm chart packaged.'
}

# Bump the chart version, write the changelog, commit and tag with cog. The hooks of cog.toml pin the images first.
def 'main release bump' []: nothing -> nothing {
    log info 'Bumping the Helm release with cog...'

    git config user.name $ci.bot_name
    git config user.email $ci.bot_email
    cog bump --auto --skip-ci | complete | check-result 'cog bump' | print

    log info 'Helm release committed and tagged.'
}

# Push the release commit and its tag to main. The push is atomic, so a rejected main leaves no tag behind.
def 'main release push' [
    tag: string # The release tag, such as helm-v1.128.0.
]: nothing -> nothing {
    log info $'Pushing Helm release ($tag)...'

    git push --atomic origin HEAD:main $tag | complete | check-result 'git push' | ignore

    log info $'Pushed Helm release ($tag).'
}

# Write the changelog section of a release to packaged/notes.md, for the release page of any forge.
def 'main release notes' [
    tag: string # The release tag, such as helm-v1.128.0.
]: nothing -> nothing {
    log info $'Writing the notes of ($tag)...'

    make-dir packaged
    cog changelog --at $tag | complete | check-result 'cog changelog' | write-text packaged/notes.md
}

# Publish the packaged chart to the OCI registry.
def 'main chart publish' []: nothing -> nothing {
    let package = glob packaged/*.tgz | first

    log info $'Publishing ($package)...'

    helm push $package $'oci://($ci.registry)/(ci-repository-owner)/charts' | complete | check-result 'helm push' | ignore

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
    | crane auth login $ci.registry --username $ci.registry_user --password-stdin
    | complete
    | check-result 'crane auth login'
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

# Print the chart version that production runs, which is the version that the tag prod points to.
def 'main chart prod' []: nothing -> nothing {
    crane-login

    print (tag-version (chart-tags (chart-repository)) prod | default 'none')
}

# Find the version to promote and say whether production runs another one. It writes the outputs `version` and `pending`.
# A run that names a version in the variable VERSION promotes that version. Another run promotes the newest chart.
def 'main chart next' []: nothing -> nothing {
    crane-login

    let tags = chart-tags (chart-repository)
    let requested = $env.VERSION?
    let version = if ($requested | is-empty) { $tags | get tag | newest-version } else { $requested }

    if $version == null or not (valid-chart-version $version) {
        fail $'No chart version to promote: ($version)'
    }

    let current = tag-version $tags prod

    write-output version $version
    write-output pending (($current != $version) | into string)

    log info $'Production runs ($current | default none). The chart to promote is ($version).'
}

# Move the tag prod of the chart to a released version. Prod Flux follows that tag.
def 'main chart promote' [
    version: string # The chart version, such as 2.13.0.
    --if-latest # Do nothing when a newer version exists, so an old approval does not undo a newer one.
]: nothing -> nothing {
    if not (valid-chart-version $version) {
        fail $'($version) is not a chart version such as 2.13.0'
    }

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

    crane tag $'($repository):($version)' prod | complete | check-result 'crane tag' | ignore

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
def 'main guard' []: nothing -> nothing {
    log info 'Checking whether a Helm release is needed...'

    let base = previous-helm-tag | default HEAD~1
    let files = git diff --name-only $base HEAD | lines
    let changed = ($files | chart-files | is-not-empty) or (images-changed $files false | is-not-empty)
    let product = commits-since $base | any {|commit| release-commit $commit }
    let release = ($env.RELEASE? | default 'true') == 'true'
    let skip = (not $release) or (not $changed) or (not $product)

    write-output skip ($skip | into string)
    log info $'Release needed: (not $skip)'
}

# Print the available CI subcommands.
def main []: nothing -> nothing {
    print 'Use a CI subcommand.'
}
