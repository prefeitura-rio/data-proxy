use std/assert
use ../lib.nu [
    chart-files
    images-changed
    latest-helm-tag
    newest-version
    release-commit
    release-tag
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
