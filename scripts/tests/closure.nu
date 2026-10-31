use std/assert
use ../closure.nu [closure-size over-limit]

# Verify that a shared path counts once and that the size is in megabytes.
export def "test closure-size-counts-a-shared-path-once" []: nothing -> nothing {
    let paths = [
        [path size];
        [/nix/store/a-one 1048576]
        [/nix/store/b-two 2097152]
        [/nix/store/a-one 1048576]
    ]

    assert equal ($paths | closure-size) 3
    assert equal ([] | closure-size) 0
}

# Verify which profiles fail.
export def "test over-limit-names-the-profiles-above-the-limit" []: nothing -> nothing {
    let limits = {console: 2900, release: 1000}

    let cases = [
        [name sizes expected];
        ['below the limit' {console: 2000} []]
        ['at the limit' {console: 2900} []]
        [
            'above the limit'
            {console: 2901}
            [
                {profile: console, size_mb: 2901, limit_mb: 2900}
            ]
        ]
        ['without a limit' {other: 99999} []]
        [
            'one of two'
            {console: 100, release: 1001}
            [
                {profile: release, size_mb: 1001, limit_mb: 1000}
            ]
        ]
    ]

    for case in $cases {
        assert equal (over-limit $case.sizes $limits) $case.expected $case.name
    }
}
