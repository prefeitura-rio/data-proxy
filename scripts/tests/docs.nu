use std/assert
use ../docs.nu [anchors link-problems links-of slug]
use ../lib.nu [remove-path write-text]

# Verify the anchor of a heading.
export def "test slug-lowercases-and-drops-punctuation" []: nothing -> nothing {
    let cases = [
        [heading anchor];
        ['Row policy' row-policy]
        ['What is it?' what-is-it]
        ['The `read_api` role' the-read_api-role]
        ['  Trim me  ' trim-me]
    ]

    for case in $cases {
        assert equal (slug $case.heading) $case.anchor
    }
}

# Verify that the headings and the links in code do not count.
export def "test code-is-not-a-heading-or-a-link" []: nothing -> nothing {
    let text = [
        '# Title'
        'See [a](a.md) and `[b](b.md)`.'
        '```nu'
        '# Not a heading'
        '[c](c.md)'
        '```'
        '## Second part'
    ]
    | str join "\n"

    assert equal (anchors $text) [
        title
        second-part
    ]
    assert equal (links-of $text) [{number: 2, target: a.md}]
}

# Verify each kind of broken link and each kind of link that passes.
export def "test link-problems-names-the-broken-links" []: nothing -> nothing {
    let dir = mktemp --directory --tmpdir

    let other = [
        '# Other'
        ''
        '## Part one'
    ]
    | str join "\n"

    let page = [
        '# Page'
        ''
        '[ok](other.md) [part](other.md#part-one) [self](#page) [web](https://example.test/x#y)'
        '[gone](missing.md)'
        '[bad](other.md#nope)'
    ]
    | str join "\n"

    $other | write-text ($dir | path join other.md)
    $page | write-text ($dir | path join page.md)

    let file = $dir | path join page.md

    assert equal (link-problems $file) [
        $'($file):4: missing.md: no such file'
        $'($file):5: other.md#nope: no such heading'
    ]

    remove-path $dir
}
