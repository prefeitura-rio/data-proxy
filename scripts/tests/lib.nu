use std/assert
use ../lib.nu [chart-files images-changed latest-helm-tag release-tag]

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
