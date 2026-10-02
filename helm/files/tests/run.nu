use std/assert
use ./lib.nu *

# Run every pure Helm helper test without Kubernetes, PostgreSQL, or network access.
def main [
    --filter: string = '' # Run only test names that match this regex.
]: nothing -> nothing {
    let tests = [
        {
            name: schema-list-returns-all-schemas
            run: { test schema-list-returns-all-schemas }
        }
        {
            name: schema-list-filters-schema
            run: { test schema-list-filters-schema }
        }
        {
            name: quote-pg-escapes-identifier-and-literal
            run: { test quote-pg-escapes-identifier-and-literal }
        }
        {
            name: sync-config-fails-for-missing-file
            run: { test sync-config-fails-for-missing-file }
        }
        {
            name: postgres-ready-args-include-dsn
            run: { test postgres-ready-args-include-dsn }
        }
        {
            name: wait-for-postgres-passes-args-to-probe
            run: { test wait-for-postgres-passes-args-to-probe }
        }
    ]

    mut passed = 0
    mut failed = 0

    for test in $tests {
        if ($filter | is-not-empty) and ($test.name !~ $filter) {
            continue
        }

        let result = try {
            do $test.run
            {passed: true, error: ''}
        } catch {|err| {passed: false, error: $err.msg} }

        if $result.passed {
            $passed += 1
            print $'✓ ($test.name)'
        } else {
            $failed += 1
            print $'✗ ($test.name)'
            print ($result.error | str trim)
        }
    }

    assert equal $failed 0
    print $'Results: ($passed) passed'
}
