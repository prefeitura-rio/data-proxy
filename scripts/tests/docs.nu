use std/assert
use ../docs.nu [
    anchors
    areas-of
    command-problems
    commands-of
    cross-problems
    length-problems
    link-problems
    links-of
    page-problems
    rules-of
    sentence-problems
    slug
]
use ../lib.nu [remove-path write-text]

# Verify the anchor of a heading.
export def "test slug-lowercases-and-drops-punctuation" []: nothing -> nothing {
    let cases = [
        [heading anchor];
        ['Row policy' row-policy]
        ['What is it?' what-is-it]
        ['The `read_api` role' the-read_api-role]
        ['  Trim me  ' trim-me]
    ]

    for case in $cases {
        assert equal (slug $case.heading) $case.anchor
    }
}

# Verify that the headings and the links in code do not count.
export def "test code-is-not-a-heading-or-a-link" []: nothing -> nothing {
    let text = [
        '# Title'
        'See [a](a.md) and `[b](b.md)`.'
        '```nu'
        '# Not a heading'
        '[c](c.md)'
        '```'
        '## Second part'
    ]
    | str join "\n"

    assert equal (anchors $text) [
        title
        second-part
    ]
    assert equal (links-of $text) [
        {number: 2, target: a.md}
    ]
}

# Verify each kind of broken link and each kind of link that passes.
export def "test link-problems-names-the-broken-links" []: nothing -> nothing {
    let dir = mktemp --directory --tmpdir

    let other = [
        '# Other'
        ''
        '## Part one'
    ]
    | str join "\n"

    let page = [
        '# Page'
        ''
        '[ok](other.md) [part](other.md#part-one) [self](#page) [web](https://example.test/x#y)'
        '[gone](missing.md)'
        '[bad](other.md#nope)'
    ]
    | str join "\n"

    $other | write-text ($dir | path join other.md)
    $page | write-text ($dir | path join page.md)

    let file = $dir | path join page.md

    assert equal (link-problems $file) [
        $'($file):4: missing.md: no such file'
        $'($file):5: other.md#nope: no such heading'
    ]

    remove-path $dir
}

# Build the record that `commands-of` returns for one command.
def found [line: int, profile: string, verb: string, ...tasks: string]: nothing -> record<line: int, profile: string, verb: string, tasks: list<string>> {
    {line: $line, profile: $profile, verb: $verb, tasks: $tasks}
}

# Verify the profile, the verb and the task names that each form of a devenv command gives.
export def "test commands-of-reads-each-form-of-a-command" []: nothing -> nothing {
    let cases = [
        [name text expected];
        [
            'a profile and a task'
            'devenv --profile integration tasks run sync:test:int'
            [(found 1 integration 'tasks run' sync:test:int)]
        ]
        [
            'no profile means default'
            'Run `devenv tasks run repo` now.'
            [(found 1 default 'tasks run' repo)]
        ]
        [
            'two tasks in backticks'
            'Use `devenv --profile repo tasks run repo:lint repo:test:unit` first.'
            [(found 1 repo 'tasks run' repo:lint repo:test:unit)]
        ]
        [
            'a word after the tasks is not a task'
            'devenv --profile integration tasks run sync:test:int and more words'
            [(found 1 integration 'tasks run' sync:test:int)]
        ]
        [
            'a full stop ends the last task'
            'devenv --profile integration tasks run sync:test:int. It takes a minute.'
            [(found 1 integration 'tasks run' sync:test:int)]
        ]
        [
            'the test verb has no tasks'
            'devenv --profile console test'
            [(found 1 console test)]
        ]
        [
            'a placeholder profile is not a command'
            'devenv --profile <name> test'
            []
        ]
        [
            'text that is not a command'
            'The devenvironment has no command here.'
            []
        ]
    ]

    for case in $cases {
        assert equal (commands-of $case.text) $case.expected $case.name
    }

    assert equal (commands-of "one\n\n```bash\ndevenv --profile console up -d\n```") [
        (found 4 console up)
    ]
}

# Verify the problems that a wrong profile or a wrong task name gives, and that a prefix of a task is valid.
export def "test command-problems-names-the-wrong-commands" []: nothing -> nothing {
    let tasks_of = {|profile|
        match $profile {
            integration => [sync:test:int proxy:test:int]
            repo => [repo:lint repo:test:unit sync:lint:py]
            _ => []
        }
    }

    let commands = [
        [file line profile verb tasks];
        [a.md 1 integration 'tasks run' [sync:test:int]]
        [a.md 2 repo 'tasks run' [repo]]
        [a.md 3 repo 'tasks run' [sync:lint]]
        [a.md 4 integration 'tasks run' [sync:tests:int]]
        [a.md 5 gone 'tasks run' [repo:lint]]
        [a.md 6 integration test []]
        [a.md 7 gone up []]
    ]

    assert equal (command-problems $commands [integration repo] $tasks_of) [
        'a.md:4: no task sync:tests:int in the profile integration'
        'a.md:5: no profile gone'
        'a.md:7: no profile gone'
    ]
}

# Build a page with the given body lines, a title and the Applies to line.
def page [...lines: string]: nothing -> string {
    ['# Page' '' 'Applies to: everything.' '' ...$lines] | str join "\n"
}

# Verify that a rule gives its ID, its length without the ID and the tag, and its tag.
export def "test rules-of-reads-the-rule-format" []: nothing -> nothing {
    let text = page '- **PY-07** Use a dataclass for plain data. [must, review]' '- **PY-08** Run `ruff` on every file. [should, ruff+pyrefly]' '- A plain bullet.' '```text' '- **PY-99** In code. [must, review]' '```'

    assert equal (rules-of $text | select id words level check) [
        [id words level check];
        [PY-07 6 must review]
        [PY-08 5 should ruff+pyrefly]
    ]
}

# Verify each problem of one page and that a valid page has none.
export def "test page-problems-names-each-problem" []: nothing -> nothing {
    let long = 1..41 | each { 'word' } | str join ' '
    let cases = [
        [name text expected];
        ['a valid page' (page '- **PY-01** Use a dataclass. [must, review]') []]
        ['a bullet that is not a rule' (page '- **PY-01** Use a dataclass. [must, review]' '- Plain bullet.') ['f.md:6: not a rule']]
        ['a rule without a tag' (page '- **PY-01** Use a dataclass.') ['f.md:5: not a rule']]
        ['a rule of 41 words' (page $'- **PY-01** ($long) [must, review]') ['f.md:5: PY-01 has 41 words, the limit is 40']]
        ['no Applies to line' "# Page\n\n- **PY-01** Use a dataclass. [must, review]" ['f.md: no "Applies to:" line']]
        ['two prefixes' (page '- **PY-01** Use a dataclass. [must, review]' '- **NU-01** Use fail. [must, review]') ['f.md: the IDs use more than one prefix: PY, NU']]
        ['an area of 2001 words' (page (1..1996 | each { 'word' } | str join ' ')) ['f.md: 2001 words, the limit is 2000']]
    ]

    for case in $cases {
        assert equal (page-problems f.md $case.text) $case.expected $case.name
    }
}

# Verify that an area is a section with a rule, and that a heading in a code fence starts no area.
export def "test areas-of-splits-the-sections-with-rules" []: nothing -> nothing {
    let text = [
        '# Style' '' '## The format' '' '1. A numbered step.' ''
        '## Python' '' 'Applies to: Python.' '' '- **PY-01** Use a dataclass. [must, review]' '```text' '## Not a heading' '```' ''
        '## Lua code' '' '- **LUA-01** Use a module. [must, review]'
    ] | str join "\n"

    let areas = areas-of $text

    assert equal ($areas | get slug) [python lua-code]
    assert (($areas | first | get text) =~ '## Not a heading')
    assert (($areas | last | get text) !~ 'PY-01')
}

# Verify that an ID defined twice and an ID that no page defines are problems.
export def "test cross-problems-names-duplicates-and-unknown-ids" []: nothing -> nothing {
    let first = page '- **PY-01** Use a dataclass. [must, review]' '- **PY-02** See PY-01 and PY-09. [must, review]'
    let second = page '- **PY-01** Use another thing. [must, review]'

    let pages = [
        {file: a.md, text: $first}
        {file: b.md, text: $second}
    ]

    let others = [
        {file: AGENTS.md, text: 'Read PY-02, PY-77 and NU-1.'}
    ]

    assert equal (cross-problems $pages $others) [
        'PY-01 is defined in a.md and in b.md'
        'a.md: cites PY-09, which no page defines'
        'AGENTS.md: cites PY-77, which no page defines'
    ]
}

def words [count: int]: nothing -> string {
    1..$count | each { 'word' } | str join ' '
}

# Verify that a sentence of 26 words is a problem, and that the text that the check skips is not.
export def "test sentence-problems-counts-the-words-of-each-sentence" []: nothing -> nothing {
    let cases = [
        {
            name: 'a sentence of 25 words'
            text: $"(words 25)."
            expected: []
        }
        {
            name: 'a sentence of 26 words'
            text: $"# Title\n\n(words 26)."
            expected: ['f.md:3: DOC-02 A sentence has 26 words.']
        }
        {
            name: 'two sentences of 20 words on one line'
            text: $"(words 20). (words 20)."
            expected: []
        }
        {
            name: 'a long sentence in a code block'
            text: $"```\n(words 40).\n```"
            expected: []
        }
        {
            name: 'a long row of a table'
            text: $"| a | b |\n| (words 40) | c |"
            expected: []
        }
        {
            name: 'a long heading'
            text: $"## (words 40)"
            expected: []
        }
        {
            name: 'a long item of a list'
            text: $"- (words 26)."
            expected: ['f.md:1: DOC-02 A sentence has 26 words.']
        }
        {
            name: 'a sentence that wraps over two lines'
            text: $"(words 14)\n(words 14)."
            expected: ['f.md:1: DOC-02 A sentence has 28 words.']
        }
        {
            name: 'a link that counts its text and not its address'
            text: ((words 23) + ' [one two](https://example.test/a/long/address/that/has/no/spaces).')
            expected: []
        }
        {
            name: 'a code span that counts as one word'
            text: ((words 24) + ' `a b c d e f`.')
            expected: []
        }
    ]

    for case in $cases {
        assert equal (sentence-problems f.md $case.text) $case.expected $case.name
    }
}

# Verify that a page of 1001 words is a problem unless it is allowed, and that a code block does not count.
export def "test length-problems-counts-the-words-of-a-page" []: nothing -> nothing {
    let cases = [
        {
            name: 'a page of 1000 words'
            file: f.md
            text: (words 1000)
            expected: []
        }
        {
            name: 'a page of 1001 words'
            file: f.md
            text: (words 1001)
            expected: ['f.md: DOC-13 The page has 1001 words. Split it by topic.']
        }
        {
            name: 'a page of 1001 words on the allowed list'
            file: allowed.md
            text: (words 1001)
            expected: []
        }
        {
            name: 'words in a code block'
            file: f.md
            text: $"(words 900)\n```\n(words 500)\n```"
            expected: []
        }
    ]

    for case in $cases {
        assert equal (length-problems $case.file $case.text allowed.md) $case.expected $case.name
    }
}
