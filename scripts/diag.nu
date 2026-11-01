use lib.nu [check-result fail read-text]

const SOURCES = 'docs/diagrams/**/*.d2'

# Return the path of the SVG file that a D2 source renders to.
def svg-of [source: path]: nothing -> path {
    $source | path parse | update extension svg | path join
}

# Render each D2 diagram to an SVG file next to its source.
def "main build" []: nothing -> nothing {
    for source in (glob $SOURCES) {
        d2 $source (svg-of $source)
    }
}

# Fail when a rendered SVG file differs from what its D2 source renders to.
def "main check" []: nothing -> nothing {
    for source in (glob $SOURCES) {
        let svg = svg-of $source
        let fresh = d2 $source - | complete | check-result $'d2 ($source)'

        if ($fresh | str trim) != (read-text $svg | str trim) {
            fail $'($svg) is out of date: run docs:build:diag'
        }
    }
}

def main []: nothing -> nothing {
    help main
}
