use std/assert
use ../lib.nu [latest-helm-tag preview-tags preview-version release-tag]

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

# Verify that each preview tag is the first seven characters of the commit and that the PostgreSQL tag keeps its prefix.
export def "test preview-tags-use-the-short-commit" []: nothing -> nothing {
    let tags = preview-tags abcdef0123456789

    assert equal $tags.PIPELINE_SHA 'abcdef0'
    assert equal $tags.PG_SHA '17-abcdef0'
    assert equal ($tags | columns | length) 5
}

# Verify the version of a preview chart with and without a release due.
export def "test preview-version-follows-the-next-release" []: nothing -> nothing {
    let cases = [
        [tag current run expected];
        [helm-v2.0.0 1.127.0 '12' '2.0.0-preview.12']
        [null 1.127.0 '3' '1.127.0-preview.3']
    ]

    for case in $cases {
        assert equal (preview-version $case.tag $case.current $case.run) $case.expected
    }
}
