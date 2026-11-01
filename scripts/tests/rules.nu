use std/assert
use ../rules.nu [commit-problems excluded line-findings RULES]

const ASSERT = {
    id: LUA-47
    globs: ['proxy/src/**/*.lua']
    pattern: '\bassert\('
    skip: '--'
    except: []
    message: 'Raise an error.'
}

const CONSTANT = {
    id: LUA-12
    globs: ['proxy/src/**/*.lua']
    pattern: '^local [A-Z][A-Z_0-9]+ ='
    skip: ''
    except: []
    message: 'Use constants.lua.'
}

# Verify that a line matches or doesn't, and that a comment line is skipped when the rule says so.
export def "test line-findings-reports-the-file-the-line-and-the-id" []: nothing -> nothing {
    let cases = [
        [name rule text expected];
        ['a call that matches' $ASSERT "local x = 1\nassert(x)" ['a.lua:2: LUA-47 Raise an error.']]
        ['a line without a match' $ASSERT "local x = 1\nreturn x" []]
        ['a comment line that the rule skips' $ASSERT '    -- assert(x)' []]
        ['two matches in one file' $ASSERT "assert(a)\nassert(b)" ['a.lua:1: LUA-47 Raise an error.' 'a.lua:2: LUA-47 Raise an error.']]
        ['a rule that skips nothing' $CONSTANT 'local LIMIT = 3' ['a.lua:1: LUA-12 Use constants.lua.']]
        ['a local with a lower case name' $CONSTANT 'local limit = 3' []]
    ]

    for case in $cases {
        assert equal (line-findings $case.rule a.lua $case.text) $case.expected $case.name
    }
}

# Verify that a rule allows the files and the folders that its path starts with.
export def "test excluded-matches-the-start-of-the-path" []: nothing -> nothing {
    let cases = [
        [name file except expected];
        ['the allowed file' proxy/src/proxy/constants.lua [proxy/src/proxy/constants.lua] true]
        ['a file in an allowed folder' scripts/tests/rules.nu [scripts/tests/] true]
        ['another file' proxy/src/proxy/cache.lua [proxy/src/proxy/constants.lua] false]
        ['a folder with a longer name' scripts/tests-old/a.nu [scripts/tests/] false]
        ['no allowed file' proxy/src/proxy/constants.lua [] false]
    ]

    for case in $cases {
        assert equal (excluded $case.file ...$case.except) $case.expected $case.name
    }
}

# Verify each commit subject that the guide forbids, and each one that it allows.
export def "test commit-problems-names-the-forbidden-subjects" []: nothing -> nothing {
    let cases = [
        [name subject expected];
        ['a feature with a scope' 'feat(proxy): add a limit' []]
        ['a breaking fix' 'fix!: change the key' []]
        ['a revert' 'revert(nix): drop the run' []]
        ['a merge commit' 'Merge branch main' []]
        ['the ci type' 'ci: speed up the job' ['ci: speed up the job: GIT-03 Use chore for CI work.']]
        ['the perf type' 'perf(sync): cap it' ['perf(sync): cap it: GIT-03 Use feat or fix for performance work.']]
        ['an unknown type' 'wip: try it' ['wip: try it: GIT-02 Use feat, fix, refactor, test, docs, chore or revert.']]
        ['an upper case summary' 'docs: Add a page' ['docs: Add a page: GIT-01 Write type(scope): summary in lower case.']]
    ]

    for case in $cases {
        assert equal (commit-problems $case.subject) $case.expected $case.name
    }
}

# Verify that each row of the table flags a line that breaks its rule and passes a line that follows it.
export def "test every-row-flags-its-violation-and-passes-the-compliant-line" []: nothing -> nothing {
    let cases = [
        [id ext bad good];
        [LUA-47 lua 'assert(value)' 'check(value)']
        [LUA-64 lua '---@diagnostic disable: missing-fields' '---@param value string']
        [LUA-09 lua 'local name = os.getenv("A")' 'local name = config.name']
        [LUA-12 lua 'local LIMIT = 3' 'local limit = 3']
        [NIX-18 nix 'x = rec { a = 1; };' 'x = { a = 1; };']
        [NIX-20 nix 'import <nixpkgs> { }' 'import ./data.nix { }']
        [NU-15 nu '    error make {msg: $message}' '    fail $message']
        [NU-16 nu '    open $file' '    read-text $file']
        [NU-16 nu '    $text | save --force $file' '    $text | write-text $file']
        [SQL-04 sql '    created_at timestamp NOT NULL' '    created_at timestamptz NOT NULL']
        [SQL-04 sql '    id bigserial PRIMARY KEY' '    id bigint GENERATED ALWAYS AS IDENTITY']
        [K6-13 js '    console.log(text)' '    info("started", {})']
        [GEN-11 nu '    # A comment in a body' '# A comment above a command']
        [GEN-11 ts "\t\t// A comment in a body" '/** A summary above a function. */']
    ]

    for case in $cases {
        let rule = $RULES
        | where id == $case.id
        | where ($it.globs | any {|glob| $glob | str ends-with $'.($case.ext)' })
        | first

        assert equal (line-findings $rule f $case.bad | length) 1 $'bad ($case.id) ($case.ext)'
        assert equal (line-findings $rule f $case.good) [] $'good ($case.id) ($case.ext)'
    }
}
