use std/log
use lib.nu [fail IMAGES latest-helm-tag make-dir read-data read-text write-text]

# CI platform configuration with GitHub Actions defaults.
let ci = {
    output_file: ($env.CI_OUTPUT_FILE? | default ($env.GITHUB_OUTPUT?))
    repository: ($env.CI_REPOSITORY? | default ($env.GITHUB_REPOSITORY?))
    registry: ($env.CI_REGISTRY? | default ghcr.io)
    registry_token: ($env.CI_REGISTRY_TOKEN? | default ($env.GITHUB_TOKEN?))
    registry_user: ($env.CI_REGISTRY_USER? | default ($env.GITHUB_ACTOR?))
    bot_name: ($env.CI_BOT_NAME? | default 'github-actions[bot]')
    bot_email: ($env.CI_BOT_EMAIL? | default 'github-actions[bot]@users.noreply.github.com')
    release_tool: ($env.CI_RELEASE_TOOL? | default gh)
}

# Return the image name from the environment, such as SYNC_IMAGE, or the default name of the image.
def image-name [image: record]: nothing -> string {
    $env | get --optional $'($image.id | str uppercase)_IMAGE' | default $image.name
}

# Return the repository owner derived from the repository path.
def ci-repository-owner []: nothing -> string {
    $ci.repository | parse '{owner}/{repo}' | first | get owner
}

# Return the value of an environment variable, or fail when it is not set.
def required-env [name: string]: nothing -> string {
    let value = $env | get --optional $name

    if $value == null {
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

# Detect changed components and write the build outputs.
def 'main changes' []: nothing -> nothing {
    log info 'Detecting changed components...'
    let force_full = ($env.FORCE_FULL? | default 'false') == 'true'
    let before = $env.BEFORE? | default HEAD~1
    let after = $env.GITHUB_SHA? | default HEAD

    let files = if $force_full {
        log info 'Force full build requested.'
        []
    } else {
        git diff --name-only $before $after | lines
    }

    let images = $IMAGES
    | where $force_full or ($files | any {$in =~ $it.change_pattern})
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

# Resolve the latest image tag from the OCI registry for one image.
def resolve-image [image: record]: nothing -> string {
    let name = image-name $image
    let fallback = $image.fallback

    log info $'Resolving latest tag for image ($name)...'

    let tag = crane ls $'($ci.registry)/(ci-repository-owner)/($name)' | lines | where $it !~ latest | get --optional 0

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

    for image in $IMAGES {
        write-output $image.id (resolve-image $image)
    }

    log info 'Resolved all image tags.'
}

# Calculate and write the next Helm chart version from commit history.
def 'main version' []: nothing -> nothing {
    log info 'Calculating the next Helm chart version...'

    let current = read-data helm/Chart.yaml | get version
    let previous = previous-helm-tag

    let range = if $previous == null {
        log warning 'No previous Helm tag found; using the last commit.'
        'HEAD~1..HEAD'
    } else {
        log info $'Previous Helm release: ($previous)'
        $'($previous)..HEAD'
    }

    let levels = git log --format=%s $range
    | lines
    | where $it !~ '\[skip ci\]'
    | each {|subject|
        match $subject {
            $header if $header =~ '^(feat|fix)(\([^)]+\))?!:' => 'major'
            $header if $header =~ '^feat(\([^)]+\))?:' => 'minor'
            $header if $header =~ '^fix(\([^)]+\))?:' => 'patch'
            _ => null
        }
    }
    | compact

    write-output current $current

    if ($levels | is-empty) {
        write-output release (false | into string)
        log info 'No release-level Conventional Commit found.'
        return
    }

    let level = [major minor patch] | where $it in $levels | first
    let next = $current | semver bump $level | to text

    write-output release (true | into string)
    write-output next $next
    write-output tag $'helm-v($next)'

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

# Set the chart version selected by the version subcommand.
def 'main chart bump' []: nothing -> nothing {
    log info $'Bumping Helm chart version to ($env.NEXT)...'

    read-text helm/Chart.yaml
    | str replace --regex '(?m)^version: .*$' $'version: ($env.NEXT)'
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
    log info 'Logging in to GHCR...'
    $ci.registry_token | helm registry login $ci.registry --username $ci.registry_user --password-stdin
    log info 'Logged in to GHCR.'
}

# Package the Helm chart into the packaged directory.
def 'main chart package' []: nothing -> nothing {
    log info 'Packaging Helm chart...'
    make-dir packaged
    helm package helm/ --destination packaged
    log info 'Helm chart packaged.'
}

# Commit and push the generated chart release.
def 'main chart commit' []: nothing -> nothing {
    log info $'Committing Helm release ($env.NEXT)...'
    git config user.name $ci.bot_name
    git config user.email $ci.bot_email
    git add helm/Chart.yaml helm/values.yaml
    git commit -m $'chore: release helm chart ($env.NEXT) [skip ci]'
    git tag $env.RELEASE_TAG
    git push origin HEAD:main
    git push origin $env.RELEASE_TAG
    log info $'Committed and pushed Helm release ($env.NEXT).'
}

# Publish the packaged chart and create its release.
def 'main chart publish' []: nothing -> nothing {
    log info $'Publishing Helm release ($env.RELEASE_TAG)...'

    let package = glob packaged/*.tgz | first
    let previous = previous-helm-tag
    let notes = if $previous == null { [] } else { [--notes-start-tag $previous] }

    helm push $package $'oci://($ci.registry)/(ci-repository-owner)/charts'
    ^($ci.release_tool) release create $env.RELEASE_TAG --title $'Helm chart ($env.RELEASE_TAG)' ...$notes --generate-notes
    ^($ci.release_tool) release upload $env.RELEASE_TAG $package

    log info $'Published Helm release ($env.RELEASE_TAG).'
}

# Decide whether a Helm release is needed.
def 'main guard' []: nothing -> nothing {
    log info 'Checking whether a Helm release is needed...'

    let previous = previous-helm-tag
    let base = $previous | default HEAD~1
    let relevant = git diff --name-only $base HEAD
    | lines
    | where { (($in | str starts-with helm/) and ($in !~ ^helm/tests/)) or $in == helm/values.yaml }
    let release = ($env.RELEASE? | default 'true') == 'true'
    let skip = (not $release) or ($relevant | is-empty)

    write-output skip ($skip | into string)
    log info $'Release needed: (not $skip)'
}

# Print the available CI subcommands.
def main []: nothing -> nothing {
    print 'Use a CI subcommand.'
}
