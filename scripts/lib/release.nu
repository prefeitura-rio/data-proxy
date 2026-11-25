use ../lib.nu [
    fail
    IMAGES
]

# Return the paths of a list that belong to the chart: the files of helm/ except its tests, and the values file.
@example "Select chart changes" { ['helm/values.yaml' 'docs/index.md'] | chart-files } --result ['helm/values.yaml']
export def chart-files []: list<string> -> list<string> {
    where (($it | str starts-with helm/) and ($it !~ ^helm/tests/)) or $it == helm/values.yaml
}

# Return the rows of IMAGES that a list of changed paths rebuilds, or every row when a full build is forced.
@example "Select every build image" { images-changed [] true | is-not-empty } --result true
export def images-changed [files: list<string>, force_full: bool]: nothing -> list<record> {
    $IMAGES | where $force_full or ($files | any {$in =~ $it.change_pattern})
}

# Return whether a release-level commit changes files outside the provider and documentation.
@example "Recognize a product feature" { release-commit {subject: 'feat: add a project', files: ['sync/src/sync/main.py']} } --result true
export def release-commit [commit: record<subject: string, files: list<string>>]: nothing -> bool {
    let level = $commit.subject =~ '^(feat|fix)(\([^)]*\))?!?: ' or $commit.subject =~ '^[a-z]+(\([^)]*\))?!: '
    let product = $commit.files | any {|file| $file !~ '^(provider/|docs/)' }

    $level and $product
}

# Return true when a text is a full chart version such as 2.10.0.
@example "Validate a chart version" { valid-chart-version '2.21.0' } --result true
export def valid-chart-version [text: string]: nothing -> bool {
    $text =~ '^\d+\.\d+\.\d+$'
}

# Fail unless a text is a full version such as 0.1.0.
@example "Accept a complete version" { check-version '2.21.0' }
export def check-version [text: string]: nothing -> nothing {
    if not (valid-chart-version $text) {
        fail $'($text) is not a version such as 0.1.0'
    }
}

# Return the newest chart version of a list of tags, or null when none is a version.
@example "Find the newest release tag" { ['2.9.0' '2.21.0' latest] | newest-version } --result '2.21.0'
export def newest-version []: list<string> -> oneof<string, nothing> {
    where (valid-chart-version $it)
    | sort-by {|tag| $tag | split row . | each {|part| $part | into int | fill --width 8 --alignment right --character 0 } | str join . }
    | last --strict 1
    | get --optional 0
}

# Return the version tag that has a digest, from rows of tag and digest, or null when none has it.
@example "Find a digest version" { version-of-digest [{tag: '2.21.0', digest: 'sha256:example'}] 'sha256:example' } --result '2.21.0'
export def version-of-digest [tags: list<record<tag: string, digest: string>>, digest: string]: nothing -> oneof<string, nothing> {
    $tags
    | where (valid-chart-version $it.tag) and digest == $digest
    | get --optional 0.tag
}

# Return the first tag of a list that is newest first and starts with the prefix, such as helm-v or terraform-v, or null.
@example "Find a Helm tag" { ['terraform-v0.2.0' 'helm-v2.21.0'] | latest-tag helm-v } --result 'helm-v2.21.0'
export def latest-tag [prefix: string]: list<string> -> oneof<string, nothing> {
    where ($it | str starts-with $prefix) | get --optional 0
}

# Export one committed Cocogitto changelog section without generating release notes again.
@example "Export tagged notes" { "## v0.3.0 - 2026-01-01\n\n- feature\n" | release-notes v0.3.0 } --result "## v0.3.0 - 2026-01-01\n\n- feature\n"
export def release-notes [tag: string]: string -> string {
    let section = $in | lines | skip until {|line| $line | str starts-with $'## ($tag) - ' }
    if ($section | is-empty) { fail $'The committed changelog has no section for ($tag)' }
    $section | skip 1 | take until {|line| $line =~ '^## |^- - -$' }
    | prepend ($section | first)
    | str join (char nl)
    | str trim --right
    | $in + (char nl)
}

# Return a stable provider version from a current or historical release tag.
@example "Read a provider version" { provider-version v0.3.0 } --result '0.3.0'
export def provider-version [tag: string]: nothing -> oneof<string, nothing> {
    $tag | parse --regex '^(?:terraform-)?v(?<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$' | get --optional 0.version
}

# Select published stable provider releases and reject duplicate semantic versions.
@example "Ignore chart releases" { [{tag_name: helm-v2.20.0, draft: false, prerelease: false}] | provider-releases } --result []
export def provider-releases []: table -> table {
    let releases = $in | each {|release|
        let version = provider-version $release.tag_name
        if $version != null and not $release.draft and not $release.prerelease {
            $release | insert version $version
        }
    } | compact

    if ($releases | group-by version | values | any {|group| ($group | length) > 1 }) {
        fail 'Multiple provider releases contain the same semantic version'
    }

    $releases
}

# Select one supported checksum manifest from the published release assets.
@example "Find default checksums" { [checksums.txt checksums.txt.sig] | provider-checksum } --result 'checksums.txt'
export def provider-checksum []: list<string> -> string {
    let matches = $in | where $it =~ '(^|_)(SHA256SUMS|checksums\.txt)$'
    if ($matches | length) != 1 { fail 'The provider release must contain one checksum manifest' }
    $matches | first
}

# Return artifact checksums indexed by filename from a checksum manifest.
@example "Read archive checksums" { 'abc  example.zip' | parse-sha256sums } --result {'example.zip': abc}
export def parse-sha256sums []: string -> record {
    parse --regex '(?m)^(?<hash>[0-9a-f]+)\s+\*?(?<name>\S+)$'
    | reduce --fold {} {|row, sums| $sums | insert $row.name $row.hash }
}

# Generate registry documents from actual published tags, checksums and archive assets.
@example "Generate registry discovery" { registry-documents {namespace: example, type: 'data-proxy', base_url: 'https://example.org/releases', key: {id: example, armor: example}, versions: []} | get --optional 0.path } --result '.well-known/terraform.json'
export def registry-documents [inputs: record]: nothing -> table<path: string, body: record> {
    let releases = $inputs.versions | each {|release|
        let sums = $release.assets | provider-checksum
        if $sums != $release.checksum_file or $'($sums).sig' not-in $release.assets {
            fail $'The checksum signature is missing for ($release.tag_name)'
        }

        let archives = $release.sums | transpose filename sha256 | where filename =~ '\.zip$' | each {|archive|
            let platform = $archive.filename | parse --regex '_(?<os>[a-z]+)_(?<arch>[a-z0-9]+)\.zip$' | get --optional 0
            if $platform == null or $archive.filename not-in $release.assets or $archive.sha256 !~ '^[0-9a-f]{64}$' {
                fail $'The provider archive is invalid or missing: ($archive.filename)'
            }
            $archive | merge $platform
        }

        if ($archives | is-empty) or (($archives | uniq-by os arch | length) != ($archives | length)) {
            fail $'The release has missing or duplicate provider platforms: ($release.tag_name)'
        }
        if ($release.assets | where $it =~ '\.zip$' | length) != ($archives | length) {
            fail $'The checksum manifest omits provider archives: ($release.tag_name)'
        }
        $release | insert archives $archives
    }
    if ($releases | group-by version | values | any {|group| ($group | length) > 1 }) {
        fail 'Multiple provider releases contain the same semantic version'
    }

    let root = $'v1/providers/($inputs.namespace)/($inputs.type)'
    let discovery = {path: '.well-known/terraform.json', body: {'providers.v1': '/v1/providers/'}}
    let versions = {
        path: $'($root)/versions'
        body: {versions: ($releases | each {|release| {version: $release.version, protocols: ['6.0'], platforms: ($release.archives | select os arch)} })}
    }
    let downloads = $releases | each {|release|
        let base = $'($inputs.base_url)/($release.tag_name)'
        $release.archives | each {|archive|
            {
                path: $'($root)/($release.version)/download/($archive.os)/($archive.arch)'
                body: {
                    protocols: ['6.0']
                    os: $archive.os
                    arch: $archive.arch
                    filename: $archive.filename
                    download_url: $'($base)/($archive.filename)'
                    shasums_url: $'($base)/($release.checksum_file)'
                    shasums_signature_url: $'($base)/($release.checksum_file).sig'
                    shasum: $archive.sha256
                    signing_keys: {gpg_public_keys: [{key_id: $inputs.key.id, ascii_armor: $inputs.key.armor}]}
                }
            }
        }
    } | flatten

    [$discovery $versions] | append $downloads
}

# Return the tag that `cog bump --dry-run` prints when a release is due, or null when it prints a message.
@example "Read a planned Helm release" { 'helm-v2.21.0' | release-tag } --result 'helm-v2.21.0'
export def release-tag []: string -> oneof<string, nothing> {
    lines | where $it =~ '^helm-v\d+\.\d+\.\d+' | get --optional 0
}

# Return the DBOS version with a major bump when sync code changes.
@example "Advance an application version" { next-dbos-version '3.0.0' true } --result '4.0.0'
export def next-dbos-version [current: string, changed: bool]: nothing -> string {
    if not $changed { return $current }

    let parts = $current | split row '.'

    if ($parts | length) != 3 or ($parts | any {|part| $part !~ '^\d+$' }) {
        fail $'The application version ($current) is not major.minor.patch'
    }

    $'(($parts | first | into int) + 1).0.0'
}

# Replace exactly one matching line in a file or fail.
@example "Update one version line" { replace-once '/tmp/data-proxy-example.txt' '^version:' 'version: 2.21.0' }
export def replace-once [file: path, pattern: string, line: string]: nothing -> nothing {
    let text = open --raw $file | decode utf-8
    let found = $text | lines | where $it =~ $pattern | length

    if $found != 1 {
        fail $'Expected one line that matches ($pattern) in ($file), found ($found)'
    }

    $text
    | lines
    | each {|row| if $row =~ $pattern { $line } else { $row } }
    | str join (char nl)
    | $in + (char nl)
    | save --force $file
}
