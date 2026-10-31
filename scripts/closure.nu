use lib.nu [check-result fail]

const MEGABYTE = 1048576
const LARGEST = 5

# Return the size of the paths in megabytes. A path that appears twice counts once.
export def closure-size []: table<path: string, size: int> -> int {
    uniq-by path | reduce --fold 0 {|row, total| $total + $row.size } | $in / $MEGABYTE | math round
}

# Return the profiles that are larger than their limit. A profile without a limit never fails.
export def over-limit [sizes: record, limits: record]: nothing -> table<profile: string, size_mb: int, limit_mb: int> {
    $sizes
    | transpose profile size_mb
    | insert limit_mb {|row| $limits | get --optional $row.profile }
    | where limit_mb != null
    | insert over {|row| $row.size_mb > $row.limit_mb }
    | where over
    | reject over
}

# Return the store paths of the shell and of the tasks of a profile, with their own size in bytes.
def profile-paths [profile: string]: nothing -> table<path: string, size: int> {
    let output = devenv --profile $profile build shell task.config
    | complete
    | check-result $'the build of the profile ($profile)'

    let built = try {
        $output | from json | values
    } catch {
        fail $'devenv gave no paths for the profile ($profile)'
    }

    nix path-info --recursive --size ...$built
    | complete
    | check-result $'the size of the profile ($profile)'
    | lines
    | parse --regex '^(?<path>\S+)\s+(?<size>\d+)$'
    | update size { into int }
}

# Fail when the closure of a profile is larger than its limit in CLOSURE_LIMITS_MB.
def main []: nothing -> nothing {
    let limits = try {
        $env.CLOSURE_LIMITS_MB | from json
    } catch {
        fail 'CLOSURE_LIMITS_MB is not a JSON object.'
    }

    let measured = $limits
    | columns
    | each {|profile|
        let paths = profile-paths $profile

        {profile: $profile, size_mb: ($paths | closure-size), paths: $paths}
    }

    let sizes = $measured | each {|row| {($row.profile): $row.size_mb} } | into record

    $measured
    | select profile size_mb
    | insert limit_mb {|row| $limits | get --optional $row.profile }
    | print

    let over = over-limit $sizes $limits

    for row in $over {
        let largest = $measured
        | where profile == $row.profile
        | first
        | get paths
        | sort-by size --reverse
        | first $LARGEST
        | each {|path|
            {
                mb: ($path.size / $MEGABYTE | math round)
                name: ($path.path | str replace --regex '^/nix/store/[a-z0-9]{32}-' '')
            }
        }

        print $'The largest paths of ($row.profile):'
        print $largest
    }

    if ($over | is-not-empty) {
        fail $'These profiles are larger than their limit: ($over | get profile | str join ", "). Remove the package that grew them, or raise the limit in nix/profiles/sizes.nix.'
    }
}
