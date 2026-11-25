use std/log
use ci/chart.nu *
use ci/promote.nu *
use ci/terraform.nu *
use ci/common.nu [ci-config previous-helm-tag write-output]
use lib.nu [IMAGES]
use lib/release.nu [chart-files images-changed]

# Return the comparison revision for a main-branch release or another CI run.
def comparison-base []: nothing -> string {
    let release = if ($env.GITHUB_REF? == refs/heads/main) { previous-helm-tag } else { null }

    if $release != null {
        git rev-list -n 1 $release | str trim
    } else {
        $env.BEFORE? | default HEAD~1
    }
}

# Detect changed components and write the build outputs.
def 'main detect changes' []: nothing -> nothing {
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
            image: $'((ci-config).registry)/((ci-config).repository)-($image.id)'
        }
    }

    let build_helm = $force_full or ($files | any {$in =~ ^helm/})

    if ($images | is-not-empty) { log info $'Detected ($images | length) image builds.' }
    if $build_helm { log info 'Helm chart build required.' }

    write-output build_helm ($build_helm | into string)
    write-output build_images (($images | is-not-empty) | into string)
    write-output build_matrix ({include: $images} | to json --raw)
}

# Print the available CI subcommands.
def main []: nothing -> nothing {
    print 'Use a CI subcommand.'
}
