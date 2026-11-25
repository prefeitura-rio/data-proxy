use std/assert
use ../lib/release.nu [provider-releases provider-version registry-documents release-notes]

# Verify native Cocogitto packages create independent versions, changelogs and hooks.
def package-versions []: nothing -> nothing {
    let root = $env.FILE_PWD | path dirname --num-levels 2

    for case in [
        {id: provider_fix, package: v, file: provider/main.go, expected: v0.2.1, changelog: provider/CHANGELOG.md, chart_hook: false}
        {id: chart_fix, package: helm-v, file: sync/src/main.py, expected: helm-v2.20.1, changelog: CHANGELOG.md, chart_hook: true}
    ] {
        let folder = mktemp --directory --tmpdir 'data-proxy-cog.XXXXXXXX'

        try {
            mkdir ($folder | path join scripts)
            cp ($root | path join cog.toml) ($folder | path join cog.toml)
            cp ($root | path join scripts changelog.tera) ($folder | path join scripts changelog.tera)
            '# Record chart preparation without accessing registries.
def "main prepare chart" [version: string]: nothing -> nothing {
    $version | save chart-hook.txt
}

# Dispatch fixture commands.
def main []: nothing -> nothing { }
' | save ($folder | path join scripts ci.nu)
            git -C $folder init --quiet
            git -C $folder config user.name 'Release test'
            git -C $folder config user.email 'release@example.org'
            git -C $folder config commit.gpgsign false
            git -C $folder config core.hooksPath /dev/null
            git -C $folder add .
            git -C $folder commit --quiet --message 'chore: initial fixture'
            for tag in [v0.2.0 terraform-v0.2.0 helm-v2.20.0] { git -C $folder tag $tag }

            let file = $folder | path join $case.file
            mkdir ($file | path dirname)
            $case.id | save $file
            git -C $folder add .
            git -C $folder commit --quiet --message $'fix: ($case.id)'
            let before = git -C $folder rev-parse HEAD | str trim
            let preview = with-env {} { cd $folder; cog bump --package $case.package --auto --dry-run | complete }
            assert equal $preview.exit_code 0 $'($case.id): ($preview.stderr)'
            assert equal ($preview.stdout | str trim) $case.expected $case.id
            assert equal (git -C $folder rev-parse HEAD | str trim) $before

            let result = with-env {} { cd $folder; cog bump --package $case.package --auto | complete }
            assert equal $result.exit_code 0 $result.stderr
            assert equal (git -C $folder tag --points-at HEAD | lines) [$case.expected] $case.id
            assert (($folder | path join $case.changelog) | path exists) $case.id
            let notes = open --raw ($folder | path join $case.changelog) | release-notes $case.expected
            let header = $notes | lines | parse '## {tag} - {date}' | get tag | first
            assert equal $header $case.expected $case.id
            let entries = $notes | lines | parse --regex '^- (?<summary>.*) \((?<commit>[0-9a-f]{7})\)$'
            assert equal $entries [{summary: $case.id, commit: ($before | str substring 0..6)}] $case.id
            assert equal (($folder | path join chart-hook.txt) | path exists) $case.chart_hook $case.id
            assert equal (git -C $folder status --porcelain | str trim) '' $case.id

            let unrelated = if $case.package == v { 'sync/other.py' } else { 'provider/other.go' }
            for path in [$unrelated docs/other.md] {
                let file = $folder | path join $path
                mkdir ($file | path dirname)
                'unrelated' | save $file
            }
            git -C $folder add .
            git -C $folder commit --quiet --message 'feat: unrelated component update'
            let ignored = with-env {} { cd $folder; cog bump --package $case.package --auto --dry-run | complete }
            assert equal $ignored.exit_code 1 $case.id
            assert equal (git -C $folder tag | lines | length) 4 $case.id
        } finally {
            rm --recursive --force $folder
        }
    }
}

# Verify registry documents use actual historical and current release filenames.
def registry-files []: nothing -> nothing {
    let legacy = 'terraform-provider-data-proxy_0.2.0_linux_amd64.zip'
    let current = 'data-proxy_0.3.0_linux_amd64.zip'
    let input = {
        namespace: example
        type: data-proxy
        base_url: 'https://example.org/releases/download'
        key: {id: fixture, armor: fixture}
        versions: [
            {version: '0.2.0', tag_name: terraform-v0.2.0, checksum_file: old_SHA256SUMS, assets: [$legacy old_SHA256SUMS old_SHA256SUMS.sig], sums: {($legacy): ('a' | fill --width 64 --character a)}}
            {version: '0.3.0', tag_name: v0.3.0, checksum_file: checksums.txt, assets: [$current checksums.txt checksums.txt.sig], sums: {($current): ('b' | fill --width 64 --character b)}}
        ]
    }
    let documents = registry-documents $input
    let old = $documents | where path == v1/providers/example/data-proxy/0.2.0/download/linux/amd64 | first | get body
    let new = $documents | where path == v1/providers/example/data-proxy/0.3.0/download/linux/amd64 | first | get body
    assert equal $old.download_url $'https://example.org/releases/download/terraform-v0.2.0/($legacy)'
    assert equal $new.download_url $'https://example.org/releases/download/v0.3.0/($current)'
    assert equal $new.shasums_url 'https://example.org/releases/download/v0.3.0/checksums.txt'
    assert equal $new.shasums_signature_url 'https://example.org/releases/download/v0.3.0/checksums.txt.sig'

    for case in [
        {id: missing_signature, assets: [$current checksums.txt]}
        {id: missing_archive, assets: [checksums.txt checksums.txt.sig]}
    ] {
        let invalid = $input | update versions [($input.versions.1? | update assets $case.assets)]
        assert error { registry-documents $invalid } $case.id
    }
}

# Verify stable release selection preserves historical providers without a release-count limit.
def release-selection []: nothing -> nothing {
    let changelog = "## v0.4.0 - 2026-01-02\n\n- new\n\n- - -\n\n## v0.3.0 - 2026-01-01\n\n- selected\n\n- - -\n\n## v0.2.0 - 2025-01-01\n\n- old\n"
    assert equal ($changelog | release-notes v0.3.0) "## v0.3.0 - 2026-01-01\n\n- selected\n"
    assert error { $changelog | release-notes v0.5.0 }

    for case in [
        {id: current, tag: v0.3.0, expected: '0.3.0'}
        {id: historical, tag: terraform-v0.2.0, expected: '0.2.0'}
        {id: chart, tag: helm-v2.20.0, expected: null}
        {id: prerelease, tag: v0.3.0-rc.1, expected: null}
        {id: leading_zero, tag: v01.2.3, expected: null}
        {id: incomplete, tag: v0.3, expected: null}
    ] {
        assert equal (provider-version $case.tag) $case.expected $case.id
    }
    let historical = {tag_name: terraform-v0.2.0, draft: false, prerelease: false}
    let current = {tag_name: v0.3.0, draft: false, prerelease: false}
    let charts = 1..220 | each {|n| {tag_name: $'helm-v2.($n).0', draft: false, prerelease: false} }
    let releases = $charts | append [$historical $current] | provider-releases
    assert equal ($releases | get version) ['0.2.0' '0.3.0']
    assert equal ([$current] | update 0.draft true | provider-releases) []
    assert error { [$current ($current | update tag_name terraform-v0.3.0)] | provider-releases }
}

# Verify publication preflight rejects invalid tags, unreviewed commits and reused versions.
def publication-gates []: nothing -> nothing {
    let root = $env.FILE_PWD | path dirname --num-levels 2
    for case in [
        {id: reviewed_tag, tag: v0.3.0, main: true, checkout: true, success: true}
        {id: missing_namespace, tag: v0.3.0, main: true, checkout: true, success: false}
        {id: historical_tag, tag: terraform-v0.3.0, main: true, checkout: true, success: false}
        {id: prerelease_tag, tag: v0.3.0-rc.1, main: true, checkout: true, success: false}
        {id: outside_main, tag: v0.3.0, main: false, checkout: true, success: false}
        {id: wrong_checkout, tag: v0.3.0, main: true, checkout: false, success: false}
        {id: reused_version, tag: v0.2.0, main: true, checkout: true, success: false}
        {id: old_version, tag: v0.1.9, main: true, checkout: true, success: false}
    ] {
        let folder = mktemp --directory --tmpdir 'data-proxy-gate.XXXXXXXX'
        try {
            let bin = $folder | path join bin
            mkdir $bin
            git -C $folder init --quiet
            git -C $folder config user.name 'Release test'
            git -C $folder config user.email 'release@example.org'
            git -C $folder config commit.gpgsign false
            git -C $folder config core.hooksPath /dev/null
            git -C $folder -c core.hooksPath=/dev/null commit --quiet --allow-empty --message 'chore: baseline'
            let base = git -C $folder rev-parse HEAD | str trim
            git -C $folder commit --quiet --allow-empty --message 'fix: provider update'
            let head = git -C $folder rev-parse HEAD | str trim
            git -C $folder update-ref refs/remotes/origin/main (if $case.main { $head } else { $base })
            git -C $folder tag $case.tag (if $case.checkout { $head } else { $base })

            let pages = $folder | path join releases.json
            let charts = 1..220 | each {|n| {tag_name: $'helm-v2.($n).0', draft: false, prerelease: false} }
            let releases = $charts | append {tag_name: terraform-v0.2.0, draft: false, prerelease: false}
            [$releases] | to json | save $pages
            let stub = $folder | path join github.nu
            '# Return fixture pages only through the paginated API.
def --wrapped main [...arguments: string]: nothing -> nothing {
    let pages = open $env.TEST_RELEASES
    if "--paginate" in $arguments and "--slurp" in $arguments {
        $pages | to json | print
    } else {
        [] | to json | print
    }
}
' | save $stub
            $'#!/bin/sh(char nl)exec ($nu.current-exe | to json --raw) --no-config-file ($stub | to json --raw) "$@"(char nl)' | save ($bin | path join gh)
            chmod +x ($bin | path join gh)
            let result = with-env {PATH: ($env.PATH | prepend $bin), TEST_RELEASES: $pages, GITHUB_REPOSITORY: 'example/fixture', GH_TOKEN: fixture, TERRAFORM_REGISTRY_HOST: registry.example.org, TERRAFORM_REGISTRY_BUCKET: fixture, TERRAFORM_REGISTRY_NAMESPACE: (if $case.id == missing_namespace { '' } else { 'example' })} {
                cd $folder
                nu --no-config-file ($root | path join scripts ci.nu) validate terraform $case.tag | complete
            }
            assert equal ($result.exit_code == 0) $case.success $'($case.id): ($result.stderr)'
        } finally {
            rm --recursive --force $folder
        }
    }
}

# Run provider release regression tests without publication requests.
def main []: nothing -> nothing {
    package-versions
    print 'PASS component versioning'
    registry-files
    print 'PASS registry compatibility'
    release-selection
    print 'PASS provider release selection'
    publication-gates
    print 'PASS publication gates'
}
