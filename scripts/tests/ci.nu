use std/assert

const SCRIPT = path self ../ci.nu

use ../lib.nu [next-dbos-version read-text remove-path replace-once write-text]

# Verify that the CI script parses. The linter does not check types, so a wrong argument type stays hidden until a run.
export def "test ci-script-parses" []: nothing -> nothing {
    let result = nu --no-config-file --commands $'source ($SCRIPT)' | complete

    assert equal $result.exit_code 0 $result.stderr
}

# Verify when the DBOS application version changes.
export def "test next-dbos-version-follows-the-code-change" []: nothing -> nothing {
    let cases = [
        [name current changed expected];
        [no-change 1.0.0 false 1.0.0]
        [change 1.0.0 true 2.0.0]
        [raises-only-the-major-number 1.4.7 true 2.0.0]
        [second-bump 2.0.0 true 3.0.0]
    ]

    for case in $cases {
        assert equal (next-dbos-version $case.current $case.changed) $case.expected $case.name
    }
}

# Verify that a version with a wrong shape stops the release.
export def "test next-dbos-version-refuses-a-wrong-shape" []: nothing -> nothing {
    for current in ['1.0' 'one.0.0' '1.0.0.1'] {
        let result = try { next-dbos-version $current true; 'no error' } catch { 'error' }

        assert equal $result 'error' $current
    }
}

# Verify that replace-once edits one line and refuses a pattern that matches zero or two times.
export def "test replace-once-needs-exactly-one-match" []: nothing -> nothing {
    let file = mktemp --tmpdir
    "a: 1\nb: 2\nb: 3\n" | write-text $file

    assert equal (try { replace-once $file '^b: ' 'b: 9'; 'no error' } catch { 'error' }) 'error' 'two matches'
    assert equal (try { replace-once $file '^c: ' 'c: 9'; 'no error' } catch { 'error' }) 'error' 'no match'

    replace-once $file '^a: ' 'a: 7'
    assert equal (read-text $file) "a: 7\nb: 2\nb: 3\n"

    remove-path $file
}

# Return the subcommands that ci.nu defines, such as `plan chart`.
def defined-commands []: nothing -> list<string> {
    read-text $SCRIPT | lines | parse --regex "^def 'main (?<name>[^']+)'" | get name
}

# Return the first two words after `ci ` or `nu scripts/ci.nu ` in a text.
def called-commands [text: string]: nothing -> list<list<string>> {
    $text
    | lines
    | parse --regex '(?:^|[\s`"-])(?:nu scripts/ci\.nu|ci) (?<words>[a-z]+(?: [a-z]+)?)'
    | get words
    | each {|words| $words | split row ' ' }
}

# Verify that every command that a workflow, an action, cog.toml or a docs page names exists in ci.nu.
export def "test every-called-ci-command-exists" []: nothing -> nothing {
    let root = $SCRIPT | path dirname | path dirname
    let defined = defined-commands
    let files = [
        ...(glob $'($root)/.github/workflows/*.yaml')
        ...(glob $'($root)/.github/actions/*/action.yaml')
        $'($root)/cog.toml'
        ...(glob $'($root)/docs/**/*.md')
    ]

    let missing = $files
    | each {|file|
        called-commands (read-text $file)
        | each {|words| $words | take 2 | str join ' ' }
        | where $it not-in $defined
        | each {|command| $'($file | path basename): ci ($command)' }
    }
    | flatten
    | uniq

    assert equal $missing [] 'A caller names a command that ci.nu does not define'
}
