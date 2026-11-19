use std/log
use ../lib.nu [checked fail]
use ../lib/release.nu [check-version newest-version valid-chart-version version-of-digest]
use common.nu [chart-repository crane-login write-output write-summary]

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

# Find the chart to promote and say whether production runs another one. It writes `version`, `current` and `pending`.
export def 'main plan promotion' []: nothing -> nothing {
    find-promotion
}

# Move the tag prod to a released chart version.
export def 'main promote chart' [
    version: string # The chart version, such as 2.13.0.
    --if-latest # Do nothing when a newer version exists, so an old approval does not undo a newer one.
]: nothing -> nothing {
    move-prod-tag $version --if-latest=$if_latest
}
