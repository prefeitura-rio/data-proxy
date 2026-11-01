use std/assert
use ../docs.nu [anchors command-problems commands-of link-problems links-of slug]
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
            'devenv --profile int tasks run sync:test:int'
            [(found 1 int 'tasks run' sync:test:int)]
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
            'devenv --profile int tasks run sync:test:int and more words'
            [(found 1 int 'tasks run' sync:test:int)]
        ]
        [
            'a full stop ends the last task'
            'devenv --profile int tasks run sync:test:int. It takes a minute.'
            [(found 1 int 'tasks run' sync:test:int)]
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

    assert equal (commands-of "one\n\n```bash\ndevenv --profile nb up -d\n```") [
        (found 4 nb up)
    ]
}

# Verify the problems that a wrong profile or a wrong task name gives, and that a prefix of a task is valid.
export def "test command-problems-names-the-wrong-commands" []: nothing -> nothing {
    let tasks_of = {|profile|
        match $profile {
            int => [sync:test:int proxy:test:int]
            repo => [repo:lint repo:test:unit sync:lint:py]
            _ => []
        }
    }

    let commands = [
        [file line profile verb tasks];
        [a.md 1 int 'tasks run' [sync:test:int]]
        [a.md 2 repo 'tasks run' [repo]]
        [a.md 3 repo 'tasks run' [sync:lint]]
        [a.md 4 int 'tasks run' [sync:tests:int]]
        [a.md 5 gone 'tasks run' [repo:lint]]
        [a.md 6 int test []]
        [a.md 7 gone up []]
    ]

    assert equal (command-problems $commands [int repo] $tasks_of) [
        'a.md:4: no task sync:tests:int in the profile int'
        'a.md:5: no profile gone'
        'a.md:7: no profile gone'
    ]
}
