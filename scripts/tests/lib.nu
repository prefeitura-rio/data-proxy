use std/assert
use ../lib.nu [
    chart-files
    images-changed
    latest-helm-tag
    latest-terraform-tag
    newest-version
    next-terraform-version
    parse-sha256sums
    provider-archive
    registry-documents
    release-commit
    release-tag
    terraform-bump
    terraform-tag-version
    valid-chart-version
    version-of-digest
]

# Verify that the newest Helm tag wins and that other tags do not count.
export def "test latest-helm-tag-skips-other-tags" []: nothing -> nothing {
    assert equal ([v1.0.0 helm-v0.2.0 helm-v0.1.0] | latest-helm-tag) 'helm-v0.2.0'
}

# Verify the first release, which has no Helm tag.
export def "test latest-helm-tag-is-null-without-a-helm-tag" []: nothing -> nothing {
    assert equal ([v1.0.0] | latest-helm-tag) null
    assert equal ([] | latest-helm-tag) null
}

# Verify that the newest provider tag wins and that the chart tags do not count.
export def "test latest-terraform-tag-skips-other-tags" []: nothing -> nothing {
    assert equal ([helm-v2.0.0 terraform-v0.2.0 terraform-v0.1.0] | latest-terraform-tag) 'terraform-v0.2.0'
    assert equal ([helm-v2.0.0] | latest-terraform-tag) null
}

# Verify the name of a provider archive, which follows the layout of a Terraform mirror.
export def "test provider-archive-follows-the-mirror-layout" []: nothing -> nothing {
    let cases = [
        [name version os arch expected];
        [data-proxy 0.1.0 linux amd64 terraform-provider-data-proxy_0.1.0_linux_amd64.zip]
        [data-proxy 1.2.3 darwin arm64 terraform-provider-data-proxy_1.2.3_darwin_arm64.zip]
    ]

    for case in $cases {
        assert equal (provider-archive $case.name $case.version $case.os $case.arch) $case.expected
    }
}

# Verify that the tag that a dry run prints is returned.
export def "test release-tag-reads-the-printed-tag" []: nothing -> nothing {
    assert equal ("helm-v2.0.0\n" | release-tag) 'helm-v2.0.0'
    assert equal ("Found feature commit abc\nhelm-v1.128.0\n" | release-tag) 'helm-v1.128.0'
}

# Verify that the message of a dry run without a release gives null.
export def "test release-tag-is-null-without-a-release" []: nothing -> nothing {
    let message = "No conventional commits for your repository that required a bump.\nPre-Hooks and Post-Hooks have been skipped.\n"

    assert equal ($message | release-tag) null
    assert equal ("" | release-tag) null
}

# Verify that only a chart file counts: a template, a helper and the values file, and not a chart test or other code.
export def "test chart-files-keeps-the-chart-and-drops-the-rest" []: nothing -> nothing {
    let cases = [
        [name files expected];
        ['a template' [helm/templates/a.yaml] [helm/templates/a.yaml]]
        ['the values file' [helm/values.yaml] [helm/values.yaml]]
        ['a chart test' [helm/tests/a_test.yaml] []]
        ['source and docs' [proxy/src/a.lua docs/a.md] []]
        ['the terraform provider' [terraform/internal/client/client.go terraform/go.mod] []]
        ['a mix' [docs/a.md helm/Chart.yaml helm/tests/a_test.yaml] [helm/Chart.yaml]]
        ['nothing' [] []]
    ]

    for case in $cases {
        assert equal ($case.files | chart-files) $case.expected $case.name
    }
}

# Verify that a release type counts only with a file outside the provider and the docs, so a provider feat never releases.
export def "test release-commit-skips-the-provider-and-the-docs" []: nothing -> nothing {
    let cases = [
        [name subject files expected];
        ['a fix of the proxy' 'fix(proxy): keep the cache key' [proxy/src/proxy/cache.lua] true]
        ['a feat without a scope' 'feat: add a setting' [helm/values.yaml] true]
        ['a breaking chore' 'chore(nix)!: drop a profile' [nix/profiles/repo.nix] true]
        ['a feat of the provider' 'feat(terraform): add a resource' [terraform/internal/client/client.go] false]
        ['a fix of the docs' 'fix(docs): correct a link' [docs/index.md llms.txt] false]
        ['a feat of the provider and its docs' 'feat(terraform): add a resource' [terraform/main.go docs/terraform.md] false]
        ['a feat of the provider and a profile' 'feat(terraform): add a task' [terraform/main.go nix/profiles/terraform.nix] true]
        ['a refactor of the sync' 'refactor(sync): split a step' [sync/src/sync/a.py] false]
        ['a release commit of the bot' 'chore(version): helm-v2.10.0 [skip ci]' [helm/Chart.yaml] false]
    ]

    for case in $cases {
        assert equal (release-commit {subject: $case.subject, files: $case.files}) $case.expected $case.name
    }
}

# Verify which images a list of paths rebuilds, and that a forced build rebuilds all of them.
export def "test images-changed-matches-the-paths-of-each-image" []: nothing -> nothing {
    let cases = [
        [name files force expected];
        ['proxy source' [proxy/src/proxy/cache.lua] false [proxy]]
        ['sync source and a migration' [sync/src/sync/a.py console/migrations/00012_a.sql] false [sync]]
        ['console app' [console/ui/src/a.ts] false [console]]
        ['a sync test' [sync/tests/unit/a.py] false []]
        ['docs only' [docs/a.md llms.txt] false []]
        ['the terraform provider and docs' [terraform/main.go terraform/go.sum docs/terraform.md] false []]
        ['no path' [] false []]
        ['no path, forced' [] true [sync proxy jobs postgres console]]
    ]

    for case in $cases {
        let ids = images-changed $case.files $case.force | get id | sort

        assert equal $ids ($case.expected | sort) $case.name
    }
}

# Verify which texts a promotion accepts as a chart version.
export def "test valid-chart-version-accepts-only-a-full-version" []: nothing -> nothing {
    let cases = [
        [text expected];
        ['2.10.0' true]
        ['0.0.1' true]
        ['2.x' false]
        ['v2.10.0' false]
        ['prod' false]
        ['2.10' false]
        ['2.10.0-rc1' false]
        ['' false]
    ]

    for case in $cases {
        assert equal (valid-chart-version $case.text) $case.expected ($case.text | to nuon)
    }
}

# Verify that the newest version compares the numbers and not the text, and that other tags do not count.
export def "test newest-version-compares-numbers" []: nothing -> nothing {
    let cases = [
        [name tags expected];
        ['two digits beat one digit' ['2.9.1' '2.10.0' '2.9.10'] '2.10.0']
        ['a patch version' ['2.10.0' '2.10.1'] '2.10.1']
        ['the tag prod does not count' [prod '2.9.0' latest] '2.9.0']
        ['no version' [prod latest] null]
        ['no tag' [] null]
    ]

    for case in $cases {
        assert equal ($case.tags | newest-version) $case.expected $case.name
    }
}

# Verify that the version of a digest is found among the version tags, and that the tag prod is not a version.
export def "test version-of-digest-finds-the-version-that-a-tag-points-to" []: nothing -> nothing {
    let tags = [
        {tag: '2.11.0', digest: 'sha256:a'}
        {tag: '2.12.0', digest: 'sha256:b'}
        {tag: prod, digest: 'sha256:a'}
    ]

    assert equal (version-of-digest $tags 'sha256:a') '2.11.0'
    assert equal (version-of-digest $tags 'sha256:b') '2.12.0'
    assert equal (version-of-digest $tags 'sha256:z') null
    assert equal (version-of-digest [] 'sha256:a') null
}

# A commit of the tests: its subject and one changed file.
def commit [subject: string, file: string = terraform/internal/client/client.go]: nothing -> record {
    {subject: $subject, files: [$file]}
}

# Verify the version bump that the commits of the provider ask for.
export def "test terraform-bump-follows-the-commit-types" []: nothing -> nothing {
    let cases = [
        [name commits expected];
        ['no commit' [] null]
        ['a fix' [(commit 'fix(terraform): stop a panic')] patch]
        ['a feature' [(commit 'feat(terraform): add a resource')] minor]
        ['a breaking change' [(commit 'refactor(terraform)!: rename an attribute')] minor]
        [
            'a feature and a fix'
            [
                (commit 'fix(terraform): a')
                (commit 'feat(terraform): b')
            ]
            minor
        ]
        [
            'types that do not release'
            [
                (commit 'chore(terraform): bump')
                (commit 'docs(terraform): text')
                (commit 'test(terraform): add')
            ]
            null
        ]
        ['a feature outside the provider' [(commit 'feat(console): add a page' console/ui/src/a.ts)] null]
        ['a fix that changes only docs' [(commit 'fix(terraform): a typo' docs/terraform.md)] null]
    ]

    for case in $cases {
        assert equal ($case.commits | terraform-bump) $case.expected $case.name
    }
}

# Verify the next provider version, which stays below 1.0 until a person sets it.
export def "test next-terraform-version-raises-the-last-version" []: nothing -> nothing {
    let cases = [
        [name last bump expected];
        ['the first release by a feature' null minor 0.1.0]
        ['the first release by a fix' null patch 0.1.0]
        ['a patch' 0.1.3 patch 0.1.4]
        ['a minor' 0.1.3 minor 0.2.0]
        ['a minor after 1.0' 1.4.2 minor 1.5.0]
    ]

    for case in $cases {
        assert equal (next-terraform-version $case.last $case.bump) $case.expected $case.name
    }
}

# Verify that the version of a provider tag is the part after the prefix.
export def "test terraform-tag-version-removes-the-prefix" []: nothing -> nothing {
    assert equal ('terraform-v0.1.3' | terraform-tag-version) '0.1.3'
}

# Verify that a checksum file becomes a record of the file names.
export def "test parse-sha256sums-reads-one-hash-for-each-file" []: nothing -> nothing {
    let text = "aaa  one.zip\nbbb  two.zip\n"

    assert equal ($text | parse-sha256sums) {'one.zip': aaa, 'two.zip': bbb}
}

# The inputs of the registry tests: a namespace and a download address that name nobody.
def registry-inputs []: nothing -> record {
    {
        namespace: example
        type: data-proxy
        base_url: 'https://git.example/org/repo/releases/download'
        key: {id: 0123456789ABCDEF, armor: 'KEY'}
        targets: [
            {os: linux, arch: amd64}
            {os: darwin, arch: arm64}
        ]
        versions: [
            {
                version: '0.1.0'
                sums: {
                    'terraform-provider-data-proxy_0.1.0_linux_amd64.zip': aaa
                    'terraform-provider-data-proxy_0.1.0_darwin_arm64.zip': bbb
                }
            }
        ]
    }
}

# Verify the files of the registry: the service discovery, the versions and one download file for each system.
export def "test registry-documents-lists-the-files-of-the-protocol" []: nothing -> nothing {
    let documents = registry-documents (registry-inputs)

    assert equal ($documents | get path | sort) [
        .well-known/terraform.json
        v1/providers/example/data-proxy/0.1.0/download/darwin/arm64
        v1/providers/example/data-proxy/0.1.0/download/linux/amd64
        v1/providers/example/data-proxy/versions
    ]
}

# Verify the content of the service discovery and the versions file.
export def "test registry-documents-describes-the-versions" []: nothing -> nothing {
    let documents = registry-documents (registry-inputs)
    let discovery = $documents | where path == .well-known/terraform.json | first | get body
    let versions = $documents | where path == v1/providers/example/data-proxy/versions | first | get body

    assert equal $discovery {'providers.v1': '/v1/providers/'}
    assert equal $versions {
        versions: [
            {
                version: '0.1.0'
                protocols: ['6.0']
                platforms: [
                    {os: linux, arch: amd64}
                    {os: darwin, arch: arm64}
                ]
            }
        ]
    }
}

# Verify that a download file names the archive, its checksum, its signature and the key.
export def "test registry-documents-describes-a-download" []: nothing -> nothing {
    let documents = registry-documents (registry-inputs)
    let path = 'v1/providers/example/data-proxy/0.1.0/download/linux/amd64'
    let body = $documents | where path == $path | first | get body
    let base = 'https://git.example/org/repo/releases/download/terraform-v0.1.0'
    let archive = 'terraform-provider-data-proxy_0.1.0_linux_amd64.zip'
    let sums = $'($base)/terraform-provider-data-proxy_0.1.0_SHA256SUMS'

    assert equal $body {
        protocols: ['6.0']
        os: linux
        arch: amd64
        filename: $archive
        download_url: $'($base)/($archive)'
        shasums_url: $sums
        shasums_signature_url: $'($sums).sig'
        shasum: aaa
        signing_keys: {
            gpg_public_keys: [
                {
                    key_id: 0123456789ABCDEF
                    ascii_armor: KEY
                }
            ]
        }
    }
}

# Verify that a release without the checksum of a system stops the registry, instead of a file without a hash.
export def "test registry-documents-fails-without-a-checksum" []: nothing -> nothing {
    let archive = 'terraform-provider-data-proxy_0.1.0_linux_amd64.zip'
    let release = {version: '0.1.0', sums: ({} | insert $archive aaa)}
    let broken = registry-inputs | update versions [$release]

    assert error { registry-documents $broken }
}
