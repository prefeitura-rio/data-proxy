use std/log
use lib.nu [
    check-result
    fail
    IMAGES
    latest-helm-tag
    make-dir
    preview-tags
    preview-version
    read-data
    read-text
    release-tag
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

# Pin the images that this commit built in helm/values.yaml, commit the change and push it to the preview branch.
def 'main preview pin' []: nothing -> nothing {
    let short = required-env GITHUB_SHA | str substring 0..<7
    let tags = preview-tags (required-env GITHUB_SHA)

    log info $'Pinning the preview images to ($short)...'

    with-env $tags { main images pin }

    git config user.name $ci.bot_name
    git config user.email $ci.bot_email
    git add helm/values.yaml
    git commit --message ('chore(preview): pin the images to ' + $short + ' [skip ci]') | complete | check-result 'git commit' | ignore

    let pulled = git pull --rebase origin preview | complete

    if $pulled.exit_code != 0 {
        # A newer commit pinned its own images first, and the run of that commit rolls the preview. Keep this pin local.
        git rebase --abort
        log warning 'A newer pin is on preview. This run does not push its pin.'
        return
    }

    git push origin HEAD:preview | complete | check-result 'git push' | ignore

    log info 'Pinned the preview images and pushed the commit.'
}

# Publish the chart of this commit as a preview version, such as 2.0.0-preview.12. It makes no commit, no tag and no release.
def 'main chart preview' []: nothing -> nothing {
    let tag = cog bump --auto --dry-run | complete | check-result 'cog bump --dry-run' | release-tag
    let version = preview-version $tag (read-data helm/Chart.yaml | get version) (required-env GITHUB_RUN_NUMBER)

    log info $'Packaging the preview chart ($version)...'
    make-dir packaged
    helm package helm/ --destination packaged --version $version | complete | check-result 'helm package' | ignore
    main chart publish
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
