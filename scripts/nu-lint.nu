use lib.nu [check-result fail]

# Run nu-lint and fail on process errors or reported findings.
def main []: nothing -> nothing {
    let findings = nu-lint --format compact --config .nu-lint.toml helm/files scripts k3d/manifests/files console/notebooks
    | complete
    | check-result 'nu-lint'
    | lines
    | where $it =~ '^\S+:\d+:\d+: '

    if ($findings | is-not-empty) {
        print ($findings | str join (char nl))
        fail $'nu-lint reported ($findings | length) findings'
    }
}
