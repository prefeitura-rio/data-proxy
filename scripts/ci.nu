use std/log
use ./lib.nu [fail]

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

# Return the configured image name from the environment or the default fallback.
def configured-image [name: string, fallback: string]: nothing -> string {
    $env | get --optional $name | default $fallback
}

# Return the repository owner derived from the repository path.
def ci-repository-owner []: nothing -> string {
    $ci.repository | parse '{owner}/{repo}' | get owner
}

# Write one CI output to the output file or print it locally.
def write-output [name: string, value: string]: nothing -> nothing {
    let line = $"($name)=($value)"
    if ($ci.output_file | is-empty) {
        print $line
    } else {
        try {
            ($line + (char nl)) | save --append $ci.output_file
        } catch {|err| fail $"Failed to write CI output ($name): ($err.msg)" {
                command: write-output
                span: (metadata $ci.output_file).span
            } }
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

    let specs = [
        {
            name: Pipeline
            containerfile: Containerfile.sync
            suffix: -sync
            tag_prefix: ""
            pattern: '^Containerfile\.sync$|^src/|^pyproject\.toml$|^uv\.lock$'
        }
        {
            name: Proxy
            containerfile: Containerfile.proxy
            suffix: -proxy
            tag_prefix: ""
            pattern: '^proxy/|^Containerfile\.proxy$'
        }
        {
            name: Jobs
            containerfile: Containerfile.jobs
            suffix: -jobs
            tag_prefix: ""
            pattern: ^Containerfile\.jobs$
        }
        {
            name: PostgreSQL
            containerfile: Containerfile.postgres
            suffix: -postgres
            tag_prefix: 17-
            pattern: ^Containerfile\.postgres$
        }
    ]

    let images = $specs
    | where $force_full or ($files | any {$in =~ $it.pattern})
    | each { insert image $"($ci.registry)/($ci.repository)($in.suffix)" }

    let build_helm = $force_full or ($files | any {$in =~ ^helm/})

    if ($images | is-not-empty) { log info $"Detected ($images | length) image builds." }
    if $build_helm { log info 'Helm chart build required.' }

    for output in [
        {
            name: build_helm
            value: ($build_helm | into string)
        }
        {
            name: build_images
            value: (($images | is-not-empty) | into string)
        }
        {
            name: build_matrix
            value: ({include: $images} | to json --raw)
        }
    ] {
        write-output $output.name $output.value
    }
}

# Resolve the latest image tag from the OCI registry for one image query.
def resolve-image [entry: record<name: string, filter: string, fallback: string>]: nothing -> string {
    log info $"Resolving latest tag for image ($entry.name)..."
    let full_image = $"($ci.registry)/(ci-repository-owner)/($entry.name)"

    let tag = try {
        crane ls $full_image
        | lines
        | where $in !~ latest
        | first
    } catch {|err| fail $"Registry query failed for image ($entry.name): ($err.msg)" {
            command: resolve-image
            span: (metadata $entry.name).span
        } }

    if ($tag | is-not-empty) and $tag != '{}' {
        log info $"Resolved tag ($tag) for image ($entry.name)."
        $tag
    } else if ($entry.fallback | is-not-empty) {
        log warning $"No tag found for image ($entry.name); using fallback ($entry.fallback)."
        $entry.fallback
    } else {
        fail $"No image tag is available for ($entry.name)" {command: resolve-image, span: (metadata $entry.name).span}
    }
}

# Resolve the latest image tags and write the resolved outputs.
def 'main images resolve' []: nothing -> nothing {
    log info 'Resolving latest image tags from GHCR...'

    let images = [
        {
            output: sync
            name: (configured-image SYNC_IMAGE data-proxy-sync)
            filter: '!= "latest"'
            fallback: latest
        }
        {
            output: postgres
            name: (configured-image POSTGRES_IMAGE data-proxy-postgres)
            filter: 'test("^17-[0-9a-f]+$")'
            fallback: ""
        }
        {
            output: jobs
            name: (configured-image JOBS_IMAGE data-proxy-jobs)
            filter: '!= "latest"'
            fallback: latest
        }
        {
            output: proxy
            name: (configured-image PROXY_IMAGE data-proxy-proxy)
            filter: '!= "latest"'
            fallback: latest
        }
    ]

    for image in $images {
        write-output $image.output (resolve-image $image)
    }

    log info 'Resolved all image tags.'
}

# Calculate and write the next Helm chart version from commit history.
def 'main version' []: nothing -> nothing {
    log info 'Calculating the next Helm chart version...'

    let current = try {
        open helm/Chart.yaml | get version
    } catch {|err| fail $"Failed to read Helm chart version: ($err.msg)" {
            command: version
            span: (metadata helm/Chart.yaml).span
        } }

    let previous = (
        git tag --sort=-v:refname
        | lines
        | where $it =~ ^helm-v
        | first
    )

    let range = if $previous == null {
        log warning 'No previous Helm tag found; using the last commit.'
        'HEAD~1..HEAD'
    } else {
        log info $"Previous Helm release: ($previous)"
        $"($previous)..HEAD"
    }

    let subjects = git log --format=%s $range | lines | where $it !~ '\[skip ci\]'

    let levels = $subjects | each {|subject|
        match $subject {
            $header if $header =~ '^(feat|fix)(\([^)]+\))?!:' => 'major'
            $header if $header =~ '^feat(\([^)]+\))?:' => 'minor'
            $header if $header =~ '^fix(\([^)]+\))?:' => 'patch'
            _ => null
        }
    } | compact

    write-output current $current

    if ($levels | is-empty) {
        write-output release (false | into string)
        log info 'No release-level Conventional Commit found.'
        return
    }

    let level = ['major' 'minor' 'patch']
    | where $it in $levels
    | first

    let next = try {
        $current | semver bump $level | to text
    } catch {
        fail $"Invalid Helm version: ($current)" {command: version, span: (metadata $current).span}
    }

    write-output release (true | into string)
    write-output next $next
    write-output tag $"helm-v($next)"

    log info $"Next Helm chart version: ($next)"
}

# Pin image tags in the chart values file.
def 'main images pin' []: nothing -> nothing {
    log info 'Pinning image tags in helm/values.yaml...'
    let replacements = [
        {
            pattern: '^  image: .*data-proxy-sync:'
            name: (configured-image SYNC_IMAGE data-proxy-sync)
            tag: $env.PIPELINE_SHA
        }
        {
            pattern: '^  image: .*data-proxy-postgres:'
            name: (configured-image POSTGRES_IMAGE data-proxy-postgres)
            tag: $env.PG_SHA
        }
        {
            pattern: '^\s+image: .*data-proxy-jobs:'
            name: (configured-image JOBS_IMAGE data-proxy-jobs)
            tag: $env.NU_SHA
        }
        {
            pattern: '^  image: .*data-proxy-proxy:'
            name: (configured-image PROXY_IMAGE data-proxy-proxy)
            tag: $env.PROXY_SHA
        }
    ]

    try {
        let values = (
            open helm/values.yaml --raw
            | lines
            | each {|line|
                $replacements | reduce --fold $line {|replacement value|
                    if $value =~ $replacement.pattern {
                        $"  image: ($ci.registry)/(ci-repository-owner)/($replacement.name):($replacement.tag)"
                    } else {
                        $value
                    }
                }
            }
            | str join (char nl)
        ) ++ (char nl)

        let pinned_count = $replacements
        | where $values =~ $"($it.name):($it.tag)"
        | length

        if $pinned_count < 4 {
            fail $"Expected at least 4 pinned image lines, found ($pinned_count)" {command: images-pin, span: (metadata helm/values.yaml).span}
        }

        $values | save --force helm/values.yaml
        log info $"Pinned ($pinned_count) image references in helm/values.yaml."
    } catch {|err| fail $"Failed to pin Helm image values: ($err.msg)" {
            command: images-pin
            span: (metadata helm/values.yaml).span
        } }
}

# Set the chart version selected by the version subcommand.
def 'main chart bump' []: nothing -> nothing {
    log info $"Bumping Helm chart version to ($env.NEXT)..."
    try {
        let chart = open helm/Chart.yaml --raw | str replace --regex '(?m)^version: .*$' $"version: ($env.NEXT)"
        $chart | save --force helm/Chart.yaml
        log info 'Helm chart version updated.'
    } catch {|err| fail $"Failed to bump Helm chart version: ($err.msg)" {
            command: chart-bump
            span: (metadata helm/Chart.yaml).span
        } }
}

# Update the checkout from main.
def 'main git update' []: nothing -> nothing {
    log info 'Updating checkout from main...'
    try {
        git pull --rebase origin main
        log info 'Checkout updated from main.'
    } catch {|err| fail $"Failed to update main: ($err.msg)" {
            command: git-update
            span: (metadata origin).span
        } }
}

# Log in to the OCI chart registry.
def 'main chart login' []: nothing -> nothing {
    log info 'Logging in to GHCR...'
    try {
        $ci.registry_token | helm registry login $ci.registry --username $ci.registry_user --password-stdin
        log info 'Logged in to GHCR.'
    } catch {|err| fail $"Failed to log in to registry: ($err.msg)" {
            command: chart-login
            span: (metadata $ci.registry).span
        } }
}

# Package the Helm chart into the packaged directory.
def 'main chart package' []: nothing -> nothing {
    log info 'Packaging Helm chart...'
    try {
        mkdir packaged
        helm package helm/ --destination packaged
        log info 'Helm chart packaged.'
    } catch {|err| fail $"Failed to package Helm chart: ($err.msg)" {
            command: chart-package
            span: (metadata helm/Chart.yaml).span
        } }
}

# Commit and push the generated chart release.
def 'main chart commit' []: nothing -> nothing {
    log info $"Committing Helm release ($env.NEXT)..."
    try {
        git config user.name $ci.bot_name
        git config user.email $ci.bot_email
        git add helm/Chart.yaml helm/values.yaml
        git commit -m $"chore: release helm chart ($env.NEXT) [skip ci]"
        git tag $env.RELEASE_TAG
        git push origin HEAD:main
        git push origin $env.RELEASE_TAG
        log info $"Committed and pushed Helm release ($env.NEXT)."
    } catch {|err| fail $"Failed to commit Helm release: ($err.msg)" {
            command: chart-commit
            span: (metadata $env.RELEASE_TAG).span
        } }
}

# Publish the packaged chart and create its release.
def 'main chart publish' []: nothing -> nothing {
    log info $"Publishing Helm release ($env.RELEASE_TAG)..."
    try {
        let package = glob packaged/*.tgz | first
        let registry = $"oci://($ci.registry)/(ci-repository-owner)/charts"

        helm push $package $registry

        let previous = git tag --sort=-v:refname | lines | where $it =~ ^helm-v | first

        let args = if $previous == null {
            [
                release
                create
                $env.RELEASE_TAG
                --title
                $"Helm chart ($env.RELEASE_TAG)"
                --generate-notes
            ]
        } else {
            [
                release
                create
                $env.RELEASE_TAG
                --title
                $"Helm chart ($env.RELEASE_TAG)"
                --notes-start-tag
                $previous
                --generate-notes
            ]
        }

        ^($ci.release_tool) ...$args
        ^($ci.release_tool) release upload $env.RELEASE_TAG $package

        log info $"Published Helm release ($env.RELEASE_TAG)."
    } catch {|err| fail $"Failed to publish Helm release: ($err.msg)" {
            command: chart-publish
            span: (metadata $env.RELEASE_TAG).span
        } }
}

# Decide whether a Helm release is needed.
def 'main guard' []: nothing -> nothing {
    log info 'Checking whether a Helm release is needed...'

    let previous = (
        git tag --sort=-v:refname
        | lines
        | where $it =~ ^helm-v
        | first
    )

    let files = if $previous == null {
        git diff --name-only HEAD~1 HEAD | lines
    } else {
        git diff --name-only $previous HEAD | lines
    }

    let relevant = $files | where { (($in | str starts-with helm/) and ($in !~ ^helm/tests/)) or $in == helm/values.yaml }
    let release = ($env.RELEASE? | default 'true') == 'true'
    let skip = (not $release) or ($relevant | is-empty)

    write-output skip ($skip | into string)
    log info $"Release needed: (not $skip)"
}

# Print the available CI subcommands.
def main []: nothing -> nothing {
    print 'Use a CI subcommand.'
}
