use std/assert
use ../diag.nu [embed-problems svg-of]

# Verify that an SVG needs exactly one page that embeds it, by a path or by a bare name.
export def "test embed-problems-names-an-svg-that-no-page-or-two-pages-embed" []: nothing -> nothing {
    let pages = [
        [file text];
        [a.md '![Overview](../diagrams/dist/overview.svg)']
        [b.md '![Flow](flow.svg) and a link to [x](other.svg)']
        [c.md '![Flow again](../diagrams/dist/flow.svg)']
    ]

    let svgs = [
        docs/diagrams/dist/overview.svg
        docs/diagrams/dist/flow.svg
        docs/diagrams/dist/other.svg
        docs/diagrams/dist/overview-2.svg
    ]

    assert equal (embed-problems $svgs $pages) [
        'docs/diagrams/dist/flow.svg is embedded by more than one page: b.md, c.md'
        'docs/diagrams/dist/other.svg is embedded by no page'
        'docs/diagrams/dist/overview-2.svg is embedded by no page'
    ]
}

# Verify that a source maps to the same name in the folder dist.
export def "test svg-of-maps-src-to-dist" []: nothing -> nothing {
    let cases = [
        [name source expected];
        ['a source' docs/diagrams/context.d2 docs/diagrams/dist/context.svg]
        ['a source with a longer name' docs/diagrams/model-all.d2 docs/diagrams/dist/model-all.svg]
    ]

    for case in $cases {
        assert equal (svg-of $case.source) $case.expected $case.name
    }
}
