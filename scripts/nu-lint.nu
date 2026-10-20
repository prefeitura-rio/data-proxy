use lib.nu [fail]

# Run nu-lint over every Nushell script of the repository, and fail when it reports a finding.
# nu-lint exits with 0 even when it finds problems, so the script reads its output.
def main []: nothing -> nothing {
    let findings = nu-lint --format compact --config .nu-lint.toml helm/files scripts k3d/manifests/files console/notebooks
    | complete
    | get stdout
    | lines
    | where $it =~ '^\S+:\d+:\d+: '

    if ($findings | is-not-empty) {
        print ($findings | str join (char nl))
        fail $'nu-lint reported ($findings | length) findings'
    }
}
