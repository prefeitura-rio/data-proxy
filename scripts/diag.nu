use lib.nu [check-result fail make-dir read-text]

const SOURCES = 'docs/diagrams/*.d2'

# Return the path of the SVG file that a D2 source renders to: the same name in the folder `dist`.
export def svg-of [source: path]: nothing -> path {
    let parts = $source | path parse

    $parts
    | update parent ($parts.parent | path join dist)
    | update extension svg
    | path join
}

# Render each D2 diagram to an SVG file next to its source.
def "main build" []: nothing -> nothing {
    for source in (glob $SOURCES) {
        make-dir (svg-of $source | path dirname)
        d2 $source (svg-of $source)
    }
}

# Return the problems of the diagrams in the pages: an SVG that no page embeds, and an SVG that more than one page embeds.
export def embed-problems [svgs: list<string>, pages: table<file: string, text: string>]: nothing -> list<string> {
    $svgs
    | each {|svg|
        let name = $svg | path basename | str replace --all '.' '\.'
        let pattern = ['!\[[^\]]*\]\((?:[^)]*/)?' $name '\)'] | str join
        let owners = $pages | where text =~ $pattern | get file

        match ($owners | length) {
            0 => [$'($svg) is embedded by no page']
            1 => []
            _ => [$'($svg) is embedded by more than one page: ($owners | str join ", ")']
        }
    }
    | flatten
}

# Fail when a rendered SVG file differs from what its D2 source renders to, or when a page embeds an SVG wrongly.
def "main check" []: nothing -> nothing {
    for source in (glob $SOURCES) {
        let svg = svg-of $source
        let fresh = d2 $source - | complete | check-result $'d2 ($source)'

        if ($fresh | str trim) != (read-text $svg | str trim) {
            fail $'($svg) is out of date: run docs:build:diag'
        }
    }

    let pages = glob docs/**/*.md | each {|file| {file: $file, text: (read-text $file)} }
    let found = embed-problems (glob docs/diagrams/dist/**/*.svg) $pages

    if ($found | is-not-empty) {
        fail $"The diagrams have problems:\n($found | str join "\n")"
    }
}

def main []: nothing -> nothing {
    help main
}
