use std/assert

const SCRIPT = path self ../ci.nu

use ../lib.nu [read-text remove-path write-text]

# Verify that the CI script parses. The linter does not check types, so a wrong argument type stays hidden until a run.
export def "test ci-script-parses" []: nothing -> nothing {
    let result = nu --no-config-file --commands $'source ($SCRIPT)' | complete

    assert equal $result.exit_code 0 $result.stderr
}

# Return the subcommands that ci.nu defines, such as `plan chart`.
def defined-commands []: nothing -> list<string> {
    [$SCRIPT ...(glob ($SCRIPT | path dirname | path join ci *.nu))]
    | each {|file| read-text $file | lines | parse --regex "^(?:export )?def 'main (?<name>[^']+)'" | get name }
    | flatten
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
