use ../lib.nu [fail IMAGES read-text write-text]

# Return the paths of a list that belong to the chart: the files of helm/ except its tests, and the values file.
export def chart-files []: list<string> -> list<string> {
    where (($it | str starts-with helm/) and ($it !~ ^helm/tests/)) or $it == helm/values.yaml
}

# Return the rows of IMAGES that a list of changed paths rebuilds, or every row when a full build is forced.
export def images-changed [files: list<string>, force_full: bool]: nothing -> list<record> {
    $IMAGES | where $force_full or ($files | any {$in =~ $it.change_pattern})
}

# Return true when a commit can release the chart: a `feat`, a `fix` or a type with `!`, as in cog.toml, that changes
# a file outside the Terraform provider, the docs and llms.txt. A provider or docs commit never releases the chart.
export def release-commit [commit: record<subject: string, files: list<string>>]: nothing -> bool {
    let level = $commit.subject =~ '^(feat|fix)(\([^)]*\))?!?: ' or $commit.subject =~ '^[a-z]+(\([^)]*\))?!: '
    let product = $commit.files | any {|file| $file !~ '^(terraform/|docs/|llms\.txt$)' }

    $level and $product
}

# Return true when a text is a full chart version such as 2.10.0. A range, a prefix or a channel tag is not one.
export def valid-chart-version [text: string]: nothing -> bool {
    $text =~ '^\d+\.\d+\.\d+$'
}

# Fail unless a text is a full version such as 0.1.0. The chart and the provider use the same form.
export def check-version [text: string]: nothing -> nothing {
    if not (valid-chart-version $text) {
        fail $'($text) is not a version such as 0.1.0'
    }
}

# Return the newest chart version of a list of tags, or null when none is a version. It compares the numbers.
export def newest-version []: list<string> -> oneof<string, nothing> {
    where (valid-chart-version $it)
    | sort-by {|tag| $tag | split row . | each {|part| $part | into int | fill --width 8 --alignment right --character 0 } | str join . }
    | last --strict 1
    | get --optional 0
}

# Return the version tag that has a digest, from rows of tag and digest, or null when none has it.
export def version-of-digest [tags: list<record<tag: string, digest: string>>, digest: string]: nothing -> oneof<string, nothing> {
    $tags
    | where (valid-chart-version $it.tag) and digest == $digest
    | get --optional 0.tag
}

# Return the first tag of a list that is newest first and starts with the prefix, such as helm-v or terraform-v, or null.
export def latest-tag [prefix: string]: list<string> -> oneof<string, nothing> {
    where ($it | str starts-with $prefix) | get --optional 0
}

# Return the file name of a provider archive. The name follows the layout of a Terraform mirror.
export def provider-archive [name: string, version: string, os: string, arch: string]: nothing -> string {
    $'terraform-provider-($name)_($version)_($os)_($arch).zip'
}

# Return the version of a provider release tag, such as 0.1.3 for terraform-v0.1.3.
export def terraform-tag-version []: string -> string {
    str replace --regex '^terraform-v' ''
}

# Return the bump that one commit subject asks for: minor for a feature or a breaking change, patch for a fix.
def commit-bump [subject: string]: nothing -> oneof<string, nothing> {
    if $subject =~ '^feat(\([^)]*\))?: ' or $subject =~ '^[a-z]+(\([^)]*\))?!: ' {
        'minor'
    } else if $subject =~ '^fix(\([^)]*\))?: ' {
        'patch'
    }
}

# Return the bump that the commits of the provider ask for, or null when none needs a release. Only a commit that
# changes a file of terraform/ counts. The major version stays 0, so a breaking change raises the minor version.
export def terraform-bump []: list<record<subject: string, files: list<string>>> -> oneof<string, nothing> {
    let bumps = $in
    | where ($it.files | any {|file| $file =~ '^terraform/' })
    | each {|commit| commit-bump $commit.subject }
    | compact

    if 'minor' in $bumps {
        'minor'
    } else if 'patch' in $bumps {
        'patch'
    }
}

# Return the next provider version from the last one, or null for the first release, and a bump.
export def next-terraform-version [last: oneof<string, nothing>, bump: string]: nothing -> string {
    if $last == null {
        return '0.1.0'
    }

    let parts = $last
    | parse --regex '^(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)$'
    | first
    | into int major minor patch

    match $bump {
        minor => $'($parts.major).($parts.minor + 1).0'
        patch => $'($parts.major).($parts.minor).($parts.patch + 1)'
        _ => (fail $'The bump ($bump) is neither minor nor patch')
    }
}

# Return a record from the file name to the hash, from the text of a SHA256SUMS file.
export def parse-sha256sums []: string -> record {
    parse --regex '(?m)^(?<hash>[0-9a-f]+)\s+\*?(?<name>\S+)$'
    | reduce --fold {} {|row, sums| $sums | insert $row.name $row.hash }
}

# Return the files of a provider registry as a table of the path and the body. The registry is static: the service
# discovery, the versions of the provider and one download file for each version, system and architecture. The inputs
# are the namespace, the type, the base URL of the release downloads, the signing key (id and armor), the targets and
# the versions with the checksums of their archives. The documents hold no host name, because the host serves them.
export def registry-documents [inputs: record]: nothing -> table<path: string, body: record> {
    let root = $'v1/providers/($inputs.namespace)/($inputs.type)'
    let discovery = {
        path: '.well-known/terraform.json'
        body: {'providers.v1': '/v1/providers/'}
    }
    let versions = {
        path: $'($root)/versions'
        body: {
            versions: (
                $inputs.versions
                | each {|release|
                    {
                        version: $release.version
                        protocols: ['6.0']
                        platforms: $inputs.targets
                    }
                }
            )
        }
    }
    let downloads = $inputs.versions | each {|release|
        let base = $'($inputs.base_url)/terraform-v($release.version)'
        let sums = $'terraform-provider-($inputs.type)_($release.version)_SHA256SUMS'

        $inputs.targets | each {|target|
            let archive = provider-archive $inputs.type $release.version $target.os $target.arch
            let hashes = $release.sums | transpose name hash | where name == $archive

            if ($hashes | is-empty) {
                fail $'The checksums of ($release.version) have no ($archive)'
            }

            {
                path: $'($root)/($release.version)/download/($target.os)/($target.arch)'
                body: {
                    protocols: ['6.0']
                    os: $target.os
                    arch: $target.arch
                    filename: $archive
                    download_url: $'($base)/($archive)'
                    shasums_url: $'($base)/($sums)'
                    shasums_signature_url: $'($base)/($sums).sig'
                    shasum: ($hashes | first | get hash)
                    signing_keys: {
                        gpg_public_keys: [
                            {
                                key_id: $inputs.key.id
                                ascii_armor: $inputs.key.armor
                            }
                        ]
                    }
                }
            }
        }
    }
    | flatten

    [$discovery $versions] | append $downloads
}

# Return the tag that `cog bump --dry-run` prints when a release is due, or null when it prints a message.
export def release-tag []: string -> oneof<string, nothing> {
    lines | where $it =~ '^helm-v\d+\.\d+\.\d+' | get --optional 0
}

# Return the next DBOS application version. It raises the major number when the code of the sync changed since the last release.
export def next-dbos-version [current: string, changed: bool]: nothing -> string {
    if not $changed { return $current }

    let parts = $current | split row '.'

    if ($parts | length) != 3 or ($parts | any {|part| $part !~ '^\d+$' }) {
        fail $'The application version ($current) is not major.minor.patch'
    }

    $'(($parts | first | into int) + 1).0.0'
}

# Replace one line pattern in a file. It fails unless the pattern matches exactly once.
export def replace-once [file: path, pattern: string, line: string]: nothing -> nothing {
    let text = read-text $file
    let found = $text | lines | where $it =~ $pattern | length

    if $found != 1 {
        fail $'Expected one line that matches ($pattern) in ($file), found ($found)'
    }

    $text
    | lines
    | each {|row| if $row =~ $pattern { $line } else { $row } }
    | str join (char nl)
    | $in + (char nl)
    | write-text $file
}
