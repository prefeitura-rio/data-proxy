use std/assert

# Run every test of the test files in its own Nushell, without Kubernetes, PostgreSQL, or network access.
def main [
    ...files: path # The test files to run. The default is lib.nu next to this script.
    --filter: string = '' # Run only test names that match this regex.
]: nothing -> nothing {
    let files = if ($files | is-empty) { [($env.FILE_PWD | path join lib.nu)] } else { $files | path expand }

    let tests = $files
    | each {|file|
        nu --no-config-file --commands $'use ($file) *; scope commands | where name starts-with "test " | get name | to json --raw'
        | from json
        | where $it =~ $filter
        | each {|name| {file: $file, name: $name} }
    }
    | flatten
    | sort-by name

    let failures = $tests
    | each {|test|
        let result = nu --no-config-file --commands $'use ($test.file) *; ($test.name)' | complete
        let passed = $result.exit_code == 0

        print $'(if $passed { "✓" } else { "✗" }) ($test.name | str replace "test " "")'

        {
            name: $test.name
            error: (if $passed { '' } else { $result.stderr | str trim })
        }
    }
    | where error != ''

    for failure in $failures {
        print $failure.error
    }

    assert equal ($failures | length) 0
    print $'Results: (($tests | length) - ($failures | length)) passed'
}
