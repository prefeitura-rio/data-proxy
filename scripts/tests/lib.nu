use std/assert
use ../lib.nu latest-helm-tag

# Verify that the newest Helm tag wins and that other tags do not count.
export def "test latest-helm-tag-skips-other-tags" []: nothing -> nothing {
    assert equal ([v1.0.0 helm-v0.2.0 helm-v0.1.0] | latest-helm-tag) 'helm-v0.2.0'
}

# Verify the first release, which has no Helm tag.
export def "test latest-helm-tag-is-null-without-a-helm-tag" []: nothing -> nothing {
    assert equal ([v1.0.0] | latest-helm-tag) null
    assert equal ([] | latest-helm-tag) null
}
