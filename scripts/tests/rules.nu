use std/assert
use ../rules.nu [
    commit-problems
    define-problems
    excluded
    file-header-problems
    function-problems
    header-problems
    input-problems
    line-findings
    migration-problems
    RULES
]

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
        [SQL-24 sql r#'  SELECT '"' || replace(p_name, '"', '""')'# '  SELECT quote_column(p_name)']
        [SQL-29 sql '-- noqa: disable=PRS,LT05' '-- noqa: disable=LT05']
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

# Verify that a header description over 40 words is a problem, and that other headers pass.
export def "test header-problems-counts-the-words-of-each-description" []: nothing -> nothing {
    let long = 1..41 | each { 'word' } | str join ' '
    let exact = 1..40 | each { 'word' } | str join ' '

    let cases = [
        [name text expected];
        ['a short description' (header 'template' 'Create a table.') []]
        ['a description of 40 words' (header 'template' $exact) []]
        ['a description of 41 words' (header 'template' $long) ['f.sql: SQL-30 The description of template has 41 words.']]
        ['a macro with a name' (header 'postgres.sql_array' $long) ['f.sql: SQL-30 The description of postgres.sql_array has 41 words.']]
        ['a file without a header' 'SELECT 1;' []]
        ['a note under the header' $"(header 'template' 'Create a table.')\n{#\n($long)\n#}" []]
    ]

    for case in $cases {
        assert equal (header-problems f.sql $case.text) $case.expected $case.name
    }
}

def header [name: string, description: string]: nothing -> string {
    {kind: template, name: $name, description: $description} | to json --indent 2 | $"{#\n($in)\n#}"
}

def function-text [lines: int, --tag: string = '$$']: nothing -> string {
    let body = 1..($lines - 3) | each { 'SELECT 1;' }

    let head = ['CREATE OR REPLACE FUNCTION data_proxy.f()' $'RETURNS void AS ($tag)']

    [...$head ...$body $'($tag);'] | str join "\n"
}

def long-message [line: int, size: int]: nothing -> string {
    $'a.sql:($line): SQL-28 The function data_proxy.f has ($size) lines.'
}

# Verify that a function over 60 lines is a problem, and that an allowed file is not.
export def "test function-problems-names-the-functions-over-60-lines" []: nothing -> nothing {
    let cases = [
        {
            name: 'a function of 60 lines'
            text: (function-text 60)
            expected: []
        }
        {
            name: 'a function of 61 lines'
            text: (function-text 61)
            expected: [(long-message 1 61)]
        }
        {
            name: 'a function with a named dollar quote'
            text: (function-text 70 --tag '$body$')
            expected: [(long-message 1 70)]
        }
        {
            name: 'a function in single quotes'
            text: "CREATE FUNCTION f() RETURNS int LANGUAGE sql AS 'SELECT 1';"
            expected: []
        }
        {
            name: 'two functions, one long'
            text: $"(function-text 10)\n\n(function-text 65)"
            expected: [(long-message 12 65)]
        }
    ]

    for case in $cases {
        assert equal (function-problems a.sql $case.text allowed.sql) $case.expected $case.name
    }

    assert equal (function-problems allowed.sql (function-text 90) allowed.sql) [] 'a function in an allowed file'
}

def migration-message [file: string, kind: string]: nothing -> string {
    let reasons = {
        changed: 'The file changed. Never edit an applied migration. Add a new one.'
        added: 'The file is not in SHA256SUMS. Run `nu scripts/rules.nu sums`.'
        missing: 'SHA256SUMS lists a file that does not exist.'
    }

    $'console/migrations/($file).sql: SQL-17 ($reasons | get --optional $kind)'
}

def migration-table [hashes: record]: nothing -> table<file: string, hash: string> {
    $hashes | items {|name, hash| {file: $'console/migrations/($name).sql', hash: $hash} }
}

# Verify each way that a migration can disagree with the list of its hash.
export def "test migration-problems-names-a-changed-new-and-missing-file" []: nothing -> nothing {
    let sums = "aaa  console/migrations/00001_a.sql\nbbb  console/migrations/00002_b.sql"

    let cases = [
        {
            name: 'every file has its hash'
            files: {'00001_a': aaa, '00002_b': bbb}
            expected: []
        }
        {
            name: 'a changed file'
            files: {'00001_a': zzz, '00002_b': bbb}
            expected: [(migration-message 00001_a changed)]
        }
        {
            name: 'a new file without a line'
            files: {'00001_a': aaa, '00002_b': bbb, '00003_c': ccc}
            expected: [(migration-message 00003_c added)]
        }
        {
            name: 'a line without a file'
            files: {'00001_a': aaa}
            expected: [(migration-message 00002_b missing)]
        }
    ]

    for case in $cases {
        assert equal (migration-problems $sums (migration-table $case.files)) $case.expected $case.name
    }
}

# Verify that a header comment of more than 40 lines is a problem, and that the lines before it don't count.
export def "test file-header-problems-counts-the-comment-lines-of-the-header" []: nothing -> nothing {
    let lines = {|count prefix| 1..$count | each { $'($prefix) line' } | str join "\n" }

    let cases = [
        [name text expected];
        ['a header of 40 lines' (do $lines 40 '#') []]
        ['a header of 41 lines' (do $lines 41 '#') ['f.nu: GEN-12 The header comment has 41 lines, the limit is 40.']]
        ['a block comment of 41 lines' (do $lines 41 ' *') ['f.nu: GEN-12 The header comment has 41 lines, the limit is 40.']]
        ['a shebang and a use line first' $"#!/usr/bin/env nu\n\nuse lib.nu [fail]\n\n(do $lines 41 '#')" ['f.nu: GEN-12 The header comment has 41 lines, the limit is 40.']]
        ['a file with no header' $"use lib.nu [fail]\n\ndef main [] {}\n(do $lines 50 '#')" []]
    ]

    for case in $cases {
        assert equal (file-header-problems f.nu $case.text) $case.expected $case.name
    }
}

# Verify that a Helm helper needs a comment above its define, and that a comment in the Helm style counts.
export def "test define-problems-names-the-helpers-without-a-comment" []: nothing -> nothing {
    let cases = [
        [name text expected];
        ['a documented helper' "{{- /* Return the name. */ -}}\n{{- define \"data-proxy.name\" -}}\nx\n{{- end }}" []]
        ['a comment in the long form' "{{- /*\nReturn the name.\n*/ -}}\n{{- define \"data-proxy.name\" -}}\nx\n{{- end }}" []]
        ['a blank line between the comment and the helper' "{{- /* Return the name. */ -}}\n\n{{- define \"data-proxy.name\" -}}\nx\n{{- end }}" []]
        ['a helper with no comment' "{{- define \"data-proxy.name\" -}}\nx\n{{- end }}" ['f.tpl:1: GEN-13 The helper data-proxy.name has no comment.']]
        ['the second helper has none' "{{- /* Return a. */ -}}\n{{- define \"a\" -}}\n{{- end }}\n\n{{- define \"b\" -}}\n{{- end }}" ['f.tpl:5: GEN-13 The helper b has no comment.']]
    ]

    for case in $cases {
        assert equal (define-problems f.tpl $case.text) $case.expected $case.name
    }
}

def input-header [inputs: list<string>, kind: string = template]: nothing -> string {
    let listed = $inputs | reduce --fold {} {|name, acc| $acc | insert $name 'A value.' }

    {kind: $kind, description: 'Do it.', inputs: $listed} | to json --indent 2 | $"{#\n($in)\n#}"
}

# Verify that a template needs an input for each variable that it uses, and that its own names are not inputs.
export def "test input-problems-names-the-variables-that-the-header-lacks" []: nothing -> nothing {
    let none = input-header []
    let schema = input-header [schema]
    let columns = input-header [columns]
    let rows = input-header [rows]
    let macro = input-header [] macro
    let missing = {|name|
        let message = $'f.sql: SQL-31 The template uses ($name), and its header has no input of that name.'

        [$message]
    }
    let import = '{% from "macros.sql" import sql_array, name_array %}{{ sql_array(rows) }}{{ name_array(rows) }}'
    let loop = '{% for c in columns %}{{ c }}{{ loop.index }}{% endfor %}'

    let cases = [
        [name text expected];
        ['every variable is an input' $"($schema)\nSELECT 1 FROM {{ schema }}.t;" []]
        ['a variable that is not an input' $"($none)\nSELECT 1 FROM {{ schema }}.t;" (do $missing schema)]
        ['a variable in a condition' $"($none)\n{% if encrypted %}x{% endif %}" (do $missing encrypted)]
        ['the list of a loop' $"($none)\n{% for c in columns %}x{% endfor %}" (do $missing columns)]
        ['a loop variable and loop' $"($columns)\n($loop)" []]
        ['a set variable' $"($none)\n{% set limit = 3 %}{{ limit }}" []]
        ['an imported macro' ([$rows $import] | str join "\n") []]
        ['a macro header' $"($macro)\n{{ anything }}" []]
        ['a variable in a Jinja comment' $"($none)\n{# {{ schema }} #}SELECT 1;" []]
        ['a file with no header' 'SELECT {{ schema }};' []]
    ]

    for case in $cases {
        assert equal (input-problems f.sql $case.text) $case.expected $case.name
    }
}
