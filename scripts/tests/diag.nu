use std/assert
use ../diag.nu [embed-problems]

# Verify that an SVG needs exactly one page that embeds it, by a path or by a bare name.
export def "test embed-problems-names-an-svg-that-no-page-or-two-pages-embed" []: nothing -> nothing {
    let pages = [
        [file text];
        [a.md '![Overview](../diagrams/a/overview.svg)']
        [b.md '![Flow](flow.svg) and a link to [x](other.svg)']
        [c.md '![Flow again](../diagrams/flow.svg)']
    ]

    let svgs = [
        docs/diagrams/a/overview.svg
        docs/diagrams/flow.svg
        docs/diagrams/other.svg
        docs/diagrams/overview-2.svg
    ]

    assert equal (embed-problems $svgs $pages) [
        'docs/diagrams/flow.svg is embedded by more than one page: b.md, c.md'
        'docs/diagrams/other.svg is embedded by no page'
        'docs/diagrams/overview-2.svg is embedded by no page'
    ]
}
