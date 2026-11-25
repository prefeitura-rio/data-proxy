use lib.nu [check-result fail]

# Validate native Nu syntax and block runtime errors while keeping suggestions advisory.
def main []: nothing -> nothing {
    let roots = [helm/files scripts k3d/manifests/files console/notebooks]
    let files = $roots | each {|root| glob $'($root)/**/*.nu' --exclude ['**/node_modules/**' '**/.venv/**'] } | flatten
    for file in $files {
        let diagnostics = nu --no-config-file --ide-check 100 $file
        | complete
        | check-result $'Nu parser for ($file)'
        | lines
        | each { from json }
        | where type == diagnostic and severity == Error

        if ($diagnostics | is-not-empty) {
            print $diagnostics
            fail $'Nu syntax errors in ($file)'
        }
    }

    let findings = nu-lint --format compact --config .nu-lint.toml ...$roots
    | complete
    | check-result 'nu-lint'
    | lines
    | where $it =~ '^\S+:\d+:\d+: '

    let visible = $findings | where $it =~ ': (warning|error)\('
    if ($visible | is-not-empty) { print ($visible | str join (char nl)) }
    if ($findings | any {$in =~ ': error\(' }) { fail 'nu-lint reported runtime errors' }
}
