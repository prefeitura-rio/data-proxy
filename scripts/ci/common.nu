use std/log
use ../lib.nu [
    checked
    fail
    IMAGES
    read-text
    write-text
]
use ../lib/release.nu [latest-tag]

# CI platform configuration with GitHub Actions defaults. It reads the environment at each call.
export def ci-config []: nothing -> record {
    {
        output_file: ($env.CI_OUTPUT_FILE? | default ($env.GITHUB_OUTPUT?))
        repository: ($env.CI_REPOSITORY? | default ($env.GITHUB_REPOSITORY?))
        registry: ($env.CI_REGISTRY? | default ghcr.io)
        registry_token: ($env.CI_REGISTRY_TOKEN? | default ($env.GITHUB_TOKEN?))
        registry_user: ($env.CI_REGISTRY_USER? | default ($env.GITHUB_ACTOR?))
        bot_name: ($env.CI_BOT_NAME? | default 'github-actions[bot]')
        bot_email: ($env.CI_BOT_EMAIL? | default 'github-actions[bot]@users.noreply.github.com')
    }
}

# Return the image name from the environment, such as SYNC_IMAGE, or the default name of the image.
export def image-name [image: record]: nothing -> string {
    $env | get --optional $'($image.id | str uppercase)_IMAGE' | default $image.name
}

# Return the repository owner derived from the repository path.
export def ci-repository-owner []: nothing -> string {
    (ci-config).repository | parse '{owner}/{repo}' | first | get owner
}

# Return the value of an environment variable, or fail when it is not set or empty.
export def required-env [name: string]: nothing -> string {
    let value = $env | get --optional $name

    if ($value | is-empty) {
        fail $'The environment variable ($name) is not set'
    }

    $value
}

# Write one CI output to the output file or print it locally.
export def write-output [name: string, value: string]: nothing -> nothing {
    let line = $'($name)=($value)'

    if ((ci-config).output_file | is-empty) {
        print $line
    } else {
        $line + (char nl) | write-text --append (ci-config).output_file
    }
}

# Return the latest Helm release tag of the repository, or null when there is none.
export def previous-helm-tag []: nothing -> oneof<string, nothing> {
    git tag --sort=-v:refname | lines | latest-tag helm-v
}

# Return the commits since a revision, without the merge commits that cog ignores, with their subjects and files.
export def commits-since [base: string]: nothing -> table<subject: string, files: list<string>> {
    git log --no-merges --format=%H $'($base)..HEAD'
    | lines
    | each {|sha|
        {
            subject: (git log -1 --format=%s $sha | str trim)
            files: (git diff-tree --no-commit-id --name-only -r $sha | lines)
        }
    }
}

# Log in to the registry for crane, when the run has a token.
export def crane-login []: nothing -> nothing {
    if ((ci-config).registry_token | is-empty) {
        return
    }

    (ci-config).registry_token
    | checked 'crane auth login' crane auth login (ci-config).registry --username (ci-config).registry_user --password-stdin
    | ignore
}

# Return the repository of the chart in the registry, such as ghcr.io/prefeitura-rio/charts/data-proxy.
export def chart-repository []: nothing -> string {
    $'((ci-config).registry)/(ci-repository-owner)/charts/data-proxy'
}

# Add a line to the summary of the job, or print it outside GitHub Actions.
export def write-summary [line: string]: nothing -> nothing {
    if ($env.GITHUB_STEP_SUMMARY? | is-empty) {
        print $line
    } else {
        $line + (char nl) | write-text --append $env.GITHUB_STEP_SUMMARY
    }
}
