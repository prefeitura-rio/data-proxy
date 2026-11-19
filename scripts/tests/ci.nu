use std/assert

const SCRIPT = path self ../ci.nu

use ../lib.nu [next-dbos-version replace-once]

# Verify that the CI script parses. The linter does not check types, so a wrong argument type stays hidden until a run.
export def "test ci-script-parses" []: nothing -> nothing {
    let result = nu --no-config-file --commands $'source ($SCRIPT)' | complete

    assert equal $result.exit_code 0 $result.stderr
}

# Verify when the DBOS application version changes.
export def "test next-dbos-version-follows-the-fingerprint" []: nothing -> nothing {
    let cases = [
        [name current stored found expected];
        [same-fingerprint 1.0.0 aaa aaa 1.0.0]
        [changed-fingerprint 1.0.0 aaa bbb 2.0.0]
        [raises-only-the-major-number 1.4.7 aaa bbb 2.0.0]
        [second-bump 2.0.0 aaa bbb 3.0.0]
    ]

    $cases | each {|case|
        assert equal (next-dbos-version $case.current $case.stored $case.found) $case.expected $case.name
    } | ignore
}

# Verify that a version with a wrong shape stops the release.
export def "test next-dbos-version-refuses-a-wrong-shape" []: nothing -> nothing {
    for current in ['1.0' 'one.0.0' '1.0.0.1'] {
        let result = try { next-dbos-version $current aaa bbb; 'no error' } catch { 'error' }

        assert equal $result 'error' $current
    }
}

# Verify that replace-once edits one line and refuses a pattern that matches zero or two times.
export def "test replace-once-needs-exactly-one-match" []: nothing -> nothing {
    let file = mktemp --tmpdir
    "a: 1\nb: 2\nb: 3\n" | save --force $file

    assert equal (try { replace-once $file '^b: ' 'b: 9'; 'no error' } catch { 'error' }) 'error' 'two matches'
    assert equal (try { replace-once $file '^c: ' 'c: 9'; 'no error' } catch { 'error' }) 'error' 'no match'

    replace-once $file '^a: ' 'a: 7'
    assert equal (open --raw $file) "a: 7\nb: 2\nb: 3\n"

    rm $file
}
