use std/assert
use ../db.nu [embed-block embed-diagram join-diagrams other-tables]

const PAGE = [
    '# Model'
    ''
    '<!-- diagram:one:start -->'
    'old one'
    '<!-- diagram:one:end -->'
    ''
    'Text between.'
    ''
    '<!-- diagram:two:start -->'
    'old two'
    '<!-- diagram:two:end -->'
]

const FENCE = "```mermaid\n%% Title\nerDiagram\n  a {}\n```"

# Verify that a diagram replaces only the block of its own name, and that a second run changes nothing.
export def "test embed-diagram-replaces-the-named-block" []: nothing -> nothing {
    let page = $PAGE | str join (char nl)

    let cases = [
        [name target old];
        ['the first block' one 'old one']
        ['the second block' two 'old two']
    ]

    for case in $cases {
        let result = embed-diagram $page $case.target Title "erDiagram\n  a {}\n"

        assert equal $result ($page | str replace $case.old $FENCE) $case.name
        assert equal (embed-diagram $result $case.target Title "erDiagram\n  a {}\n") $result $'($case.name) twice'
    }
}

# Verify that a missing marker is an error and not a silent no-op.
export def "test embed-diagram-fails-without-a-marker" []: nothing -> nothing {
    let failed = try {
        embed-diagram '# Model' one Title 'erDiagram'
        false
    } catch {
        true
    }

    assert $failed
}

# Verify that the joined diagram has one header and the lines of every part.
export def "test join-diagrams-keeps-one-header" []: nothing -> nothing {
    let one = "erDiagram\n\n\"a\" {\n  text x\n}\n"
    let first = "erDiagram\n\"a\" {\n}\n"
    let second = "erDiagram\n\"b\" {\n}\n"

    let cases = [
        {
            name: 'one part'
            parts: [$one]
            expected: "erDiagram\n\"a\" {\n  text x\n}"
        }
        {
            name: 'two parts'
            parts: [$first $second]
            expected: "erDiagram\n\"a\" {\n}\n\"b\" {\n}"
        }
    ]

    for case in $cases {
        assert equal (join-diagrams ...$case.parts) $case.expected $case.name
    }
}

# Verify that a block of any marker name is replaced as plain text.
export def "test embed-block-replaces-plain-text" []: nothing -> nothing {
    let page = "# Model\n\n<!-- tables:other:start -->\nold\n<!-- tables:other:end -->\n\nEnd."
    let expected = "# Model\n\n<!-- tables:other:start -->\n| A |\n<!-- tables:other:end -->\n\nEnd."

    assert equal (embed-block $page tables:other '| A |') $expected
}

# Verify that the other tables are the base tables that no diagram draws, with their comment and group.
export def "test other-tables-lists-the-tables-that-no-diagram-draws" []: nothing -> nothing {
    let tables = [
        {
            name: dbos.queues
            type: 'BASE TABLE'
            comment: 'The queues.'
            labels: [
                {name: DBOS, virtual: true}
            ]
        }
        {
            name: dbos.notifications
            type: 'BASE TABLE'
            comment: 'Messages.'
            labels: [
                {name: DBOS, virtual: true}
            ]
        }
        {
            name: console.runs
            type: VIEW
            comment: 'A view.'
        }
        {
            name: test.ducklake_x
            type: 'BASE TABLE'
            comment: ''
        }
        {
            name: test.ducklake_snapshot
            type: 'BASE TABLE'
            comment: 'Snapshots.'
            labels: [
                {name: 'DuckLake metadata', virtual: true}
            ]
        }
    ]

    let cases = [
        {
            name: 'a drawn table, a view and a table without a comment'
            shown: [dbos.queues]
            expected: [
                {Group: '-', Table: '`ducklake_x`', Contents: '-'}
                {Group: DBOS, Table: '`notifications`', Contents: Messages.}
                {Group: 'DuckLake metadata', Table: '`ducklake_snapshot`', Contents: Snapshots.}
            ]
        }
        {
            name: 'every table is drawn'
            shown: [
                dbos.queues
                dbos.notifications
                test.ducklake_x
                test.ducklake_snapshot
            ]
            expected: []
        }
    ]

    for case in $cases {
        assert equal (other-tables $tables ...$case.shown) $case.expected $case.name
    }
}
