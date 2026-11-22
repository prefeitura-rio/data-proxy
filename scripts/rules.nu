use lib.nu [checked fail   ]

const SKIPPED_FOLDERS = [
    '**/node_modules/**'
    '**/.devenv/**'
    '**/.venv/**'
    '**/.lux/**'
    '**/__pycache__/**'
]

# Historical commit subjects exempt from validation until they leave the last 50 commits.
const ALLOWED_SUBJECTS = [
    'chore: Configure Renovate (#5)'
]

const HEADER = '(?s)\{#\n(\{.*?\})\n#\}'
const HEADER_WORDS = 40
const FUNCTION_LINES = 60
const HEADER_LINES = 40
const MIGRATIONS = 'migrations'
const SUMS = 'migrations/SHA256SUMS'
const LUA = ['proxy/src/**/*.lua']
const NIX = ['**/*.nix']

const NU = [
    'scripts/**/*.nu'
    'helm/files/scripts/**/*.nu'
    'console/notebooks/*.nu'
]

const SQL = [
    'sync/src/sync/templates/**/*.sql'
    'helm/files/templates/**/*.sql'
    'migrations/*/*.sql'
]

const PARSER_DISABLED = [
    migrations/
    sync/src/sync/templates/duckdb/
    sync/src/sync/templates/postgres/create_table_changes_function.sql
    sync/src/sync/templates/postgres/delete_removed_state.sql
    sync/src/sync/templates/postgres/views/create_function.sql
    sync/tests/templates/duckdb/publish_people_snapshots.sql
]

const LONG_FUNCTIONS = [
    migrations/console/00011_console_config.sql
    migrations/console/00018_rls_groups.sql
    sync/src/sync/templates/postgres/views/create_aggregate_function.sql
    sync/src/sync/templates/postgres/views/create_function.sql
    sync/src/sync/templates/postgres/views/routing/filter.sql
]

const HEADED = [
    'scripts/**/*.nu'
    'helm/files/scripts/**/*.nu'
    'console/notebooks/*.nu'
    'console/ui/*.ts'
    'k6/**/*.js'
    'proxy/lux.toml'
]

const CHART_HELPERS = 'helm/templates/_*.tpl'

const BUILTINS = [
    loop
    true
    false
    none
    range
    namespace
    caller
    varargs
    kwargs
    not
    and
    or
    in
    is
    if
    else
]

const NU_LIBS = [
    scripts/lib.nu
    helm/files/scripts/lib.nu
]

export const RULES = [
    [id, globs, pattern, skip, except, message];
    [LUA-47, $LUA, '\bassert\(', '--', [], "Check the value and raise an error with a message."]
    [LUA-64, $LUA, '---@diagnostic disable', '', [], "Fix the cause and don't disable a diagnostic."]
    [LUA-09, $LUA, 'os\.getenv', '--', [proxy/src/proxy/main.lua], "Read a setting only from the config table."]
    [LUA-12, $LUA, '^local [A-Z][A-Z_0-9]+ =', '', [proxy/src/proxy/constants.lua], "Define the constant in constants.lua."]
    [NIX-18, $NIX, '\brec \{|= rec ', '#', [], "Bind the shared names in a let block."]
    [NIX-20, $NIX, '\bimport <[a-z]+>', '#', [], "Don't use a lookup path."]
    [NU-15, $NU, '\berror make\b', '#', [...$NU_LIBS console/notebooks/build.nu], "Stop with fail from lib.nu."]
    [SQL-04, $SQL, '^\s+\w+\s+(timestamp(?! with)|(big)?serial)\b', '--', [], "Use timestamptz and bigint GENERATED ALWAYS AS IDENTITY for a column."]
    [SQL-24, $SQL, r#'"' \|\| replace\('#, '--', [sync/src/sync/templates/postgres/views/routing/quoting.sql], "Quote a DuckDB identifier with quote_column()."]
    [SQL-29, [...$SQL 'sync/tests/templates/**/*.sql'], '^-- noqa: disable=[A-Z0-9,]*PRS', '', $PARSER_DISABLED, "Make SQLFluff parse the template, or add it to the allowed list with a reason."]
    [GEN-11, $NU, '^\s{4,}#\s', '', [], "Write no comment inside a function body."]
    [GEN-11, ['console/ui/src/**/*.ts'], '^\t{2,}(//|/\*)', '', [], "Write no comment inside a function body."]
]

# Return rule violations with file paths, line numbers and messages.
@example "Report a matching source rule" { line-findings {id: DEMO, pattern: bad, skip: '#', message: 'Avoid bad'} 'example.nu' 'bad' } --result ['example.nu:1: DEMO Avoid bad']
export def line-findings [rule: record, file: string, text: string]: nothing -> list<string> {
    $text
    | lines
    | enumerate
    | where ($rule.skip | is-empty) or not ($it.item | str trim | str starts-with $rule.skip)
    | where item =~ $rule.pattern
    | each {|row| $'($file):($row.index + 1): ($rule.id) ($rule.message)' }
}

# Return the number of words of the description of a header, or 0 for a header without one.
def description-words [header: record]: nothing -> int {
    let description = $header | get --optional description

    if $description == null {
        0
    } else {
        $description | split row --regex '\s+' | length
    }
}

# Return a problem for each header of a template whose description has more than 40 words.
@example "Check an unannotated SQL statement" { header-problems 'example.sql' 'SELECT 1;' } --result []
export def header-problems [file: string, text: string]: nothing -> list<string> {
    $text
    | parse --regex $HEADER
    | get capture0
    | each {|source|
        let header = try { $source | from json } catch { fail $'($file): The header is not valid JSON.' }

        {
            name: ($header | get --optional name | default template)
            words: (description-words $header)
        }
    }
    | where words > $HEADER_WORDS
    | each {|header| $'($file): SQL-30 The description of ($header.name) has ($header.words) words.' }
}

# Report file header comments that exceed 40 lines.
@example "Check a short file header" { file-header-problems 'example.nu' '# A short header.' } --result []
export def file-header-problems [file: string, text: string]: nothing -> list<string> {
    let size = $text
    | lines
    | where not ($it =~ '^(#!|use |\s*$)')
    | take while {|line| $line =~ '^\s*(#|//|/\*|\*|--)' }
    | length

    if $size > $HEADER_LINES {
        [
            $'($file): GEN-12 The header comment has ($size) lines, the limit is ($HEADER_LINES).'
        ]
    } else {
        []
    }
}

# Return a problem for each `define` of a Helm helper that has no comment above it.
@example "Check an empty helper file" { define-problems 'example.tpl' '' } --result []
export def define-problems [file: string, text: string]: nothing -> list<string> {
    let lines = $text | lines

    $lines
    | enumerate
    | where item =~ '^\{\{-?\s*define "'
    | each {|row|
        let before = $lines | first $row.index | where ($it | str trim | is-not-empty) | last 1

        if ($before | is-empty) or (($before | first) !~ '\*/\s*-?\}\}\s*$') {
            let name = $row.item | parse --regex 'define "(?<name>[^"]+)"' | first | get name

            $'($file):($row.index + 1): GEN-13 The helper ($name) has no comment.'
        }
    }
    | compact
}

# Return the text of the first group of a pattern, for each match in a text.
def captures [text: string, pattern: string]: nothing -> list<string> {
    $text | parse --regex $pattern | get --optional capture0 | default []
}

# Return the names in a comma-separated list, without the default value of each.
def names-in [list: string]: nothing -> list<string> {
    $list | split row ',' | each {|item| $item | str replace --regex '=.*$' '' | str trim }
}

# Report template variables missing from header inputs and local definitions.
@example "Check a literal SQL statement" { input-problems 'example.sql' 'SELECT 1;' } --result []
export def input-problems [file: string, text: string]: nothing -> list<string> {
    let headers = $text | parse --regex $HEADER

    if ($headers | is-empty) {
        return []
    }

    let header = try {
        $headers | first | get capture0 | from json
    } catch {
        return []
    }

    if ($header | get --optional kind) == macro {
        return []
    }

    let listed = $header | get --optional inputs | default {} | columns
    let code = $text | str replace --all --regex '(?s)\{#.*?#\}' ''
    let tag = '\{%-?\s*'
    let name = '([A-Za-z_]\w*)'

    let defined = [
        (captures $code ([$tag 'set\s+' $name] | str join))
        (captures $code ([$tag 'for\s+' $name] | str join))
        (captures $code ([$tag 'for\s+\w+,\s*' $name] | str join))
        (captures $code ([$tag 'macro\s+' $name] | str join))
        (
            captures $code ([$tag 'macro\s+\w+\(([^)]*)\)'] | str join)
            | each {|args| names-in $args }
            | flatten
        )
        (
            captures $code ([$tag 'from\s+"[^"]+"\s+import\s+([^%]+)%'] | str join)
            | each {|names| names-in $names }
            | flatten
        )
        (captures $code ([$tag 'import\s+"[^"]+"\s+as\s+' $name] | str join))
    ]
    | flatten

    let used = [
        (captures $code (['\{\{-?\s*' $name] | str join))
        (captures $code ([$tag '(?:if|elif)\s+' $name] | str join))
        (captures $code ([$tag 'for\s+\w+(?:,\s*\w+)?\s+in\s+' $name] | str join))
    ]
    | flatten

    $used
    | uniq
    | where $it not-in $listed and $it not-in $defined and $it not-in $BUILTINS
    | each {|variable| $'($file): SQL-31 The template uses ($variable), and its header has no input of that name.' }
}

# Return the closing line index of a SQL function.
def function-end [lines: list<string>, start: int]: nothing -> int {
    mut index = $start
    mut tag = ''
    mut seen = 0

    while $index < ($lines | length) {
        let line = $lines | skip $index | first

        if $tag == '' {
            let opened = $line | parse --regex '(?<tag>\$[A-Za-z_]*\$)'

            if ($opened | is-not-empty) {
                $tag = ($opened | first | get tag)
            } else if ($line | str ends-with ';') {
                return $index
            }
        }

        if $tag != '' {
            $seen += ($line | split row $tag | length) - 1

            if $seen > 0 and $seen mod 2 == 0 {
                return $index
            }
        }

        $index += 1
    }

    $index - 1
}

# Return a problem for each function over 60 lines in the text of a SQL file, unless the file is allowed.
@example "Check a short SQL function" { function-problems 'example.sql' 'CREATE FUNCTION example() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;' } --result []
export def function-problems [file: string, text: string, ...allowed: string]: nothing -> list<string> {
    if (excluded $file ...$allowed) {
        return []
    }

    let lines = $text | lines

    $lines
    | enumerate
    | where item =~ '^\s*CREATE (OR REPLACE )?FUNCTION'
    | each {|row|
        let size = (function-end $lines $row.index) - $row.index + 1
        let names = $row.item | parse --regex 'FUNCTION\s+(?<name>.+?)\s*\('
        let name = if ($names | is-empty) { 'function' } else { $names | first | get name }

        {line: ($row.index + 1), name: $name, size: $size}
    }
    | where size > $FUNCTION_LINES
    | each {|function| $'($file):($function.line): SQL-28 The function ($function.name) has ($function.size) lines.' }
}

# Return a problem for each migration that disagrees with the list of hashes: a changed, a new and a missing file.
@example "Validate matching migration checksums" { migration-problems 'abc  migrations/example.sql' [{file: 'migrations/example.sql', hash: abc}] } --result []
export def migration-problems [sums: string, files: table<file: string, hash: string>]: nothing -> list<string> {
    let listed = $sums
    | lines
    | where ($it | str trim | is-not-empty)
    | parse --regex '(?<hash>\S+)\s+(?<file>\S+)'

    let changed = $files
    | join $listed file
    | where hash != $it.hash_
    | each {|row| $'($row.file): SQL-17 The file changed. Never edit an applied migration. Add a new one.' }

    let added = $files
    | where file not-in ($listed | get file)
    | each {|row| $'($row.file): SQL-17 The file is not in SHA256SUMS. Run `nu scripts/rules.nu sums`.' }

    let missing = $listed
    | where file not-in ($files | get file)
    | each {|entry| $'($entry.file): SQL-17 SHA256SUMS lists a file that does not exist.' }

    [...$changed ...$added ...$missing]
}

# Return the reserved project names that a source lists, from the text of the file.
def reserved-names [text: string, list: string, name: string]: nothing -> list<string> {
    $text
    | parse --regex $list
    | get --optional body
    | default []
    | each {|body| $body | parse --regex $name | get name }
    | flatten
    | sort
}

# Return a problem for each reserved project name that one of the three lists lacks: the console, the Terraform provider and the proxy.
@example "Compare reserved project names" { reserved-project-problems ((open --raw 'console/ui/src/lib/projects.ts' | decode utf-8)) ((open --raw 'provider/internal/provider/names.go' | decode utf-8)) ((open --raw 'proxy/src/proxy/constants.lua' | decode utf-8)) }
export def reserved-project-problems [console: string, provider: string, proxy: string]: nothing -> list<string> {
    let lists = [
        [file names];
        [console/ui/src/lib/projects.ts (reserved-names $console '(?s)RESERVED_PROJECTS = \[(?<body>[^\]]*)\]' "'(?<name>\\w+)'")]
        [provider/internal/provider/names.go (reserved-names $provider '(?s)func reservedProjects\(\) \[\]string \{\s*return \[\]string\{(?<body>[^}]*)\}' '"(?<name>\w+)"')]
        [proxy/src/proxy/constants.lua (reserved-names $proxy '(?s)reserved_projects = \{(?<body>.*?)\n\t\}' '(?<name>\w+) = true')]
    ]

    let every = $lists | get names | flatten | uniq

    $lists
    | each {|list|
        if ($list.names | is-empty) {
            [$'($list.file): GEN-06 The file lists no reserved project name.']
        } else {
            $every
            | where $it not-in $list.names
            | each {|name| $'($list.file): GEN-06 The list of reserved project names lacks ($name).' }
        }
    }
    | flatten
}

# Return the files of the migrations with the hash of each one.
def migration-files []: nothing -> table<file: string, hash: string> {
    glob $'($MIGRATIONS)/*/*.sql'
    | each {|file| $file | path relative-to (pwd) }
    | sort
    | each {|file| {file: $file, hash: ((open --raw $file | decode utf-8) | hash sha256)} }
}

# Return true when the path starts with one of the files or folders that the rule allows.
@example "Match an allowed directory" { excluded 'migrations/console/example.sql' 'migrations/' } --result true
export def excluded [file: string, ...except: string]: nothing -> bool {
    $except | any {|head| $file | str starts-with $head }
}

# Return repository-specific commit type and summary violations.
@example "Check commit policy" { commit-policy-problems [fix] 'fix: validate schema names' } --result []
export def commit-policy-problems [types: list<string>, ...subjects: string]: nothing -> list<string> {
    $subjects
    | where $it not-in $ALLOWED_SUBJECTS
    | each {|subject|
        let kind = $subject | split row --regex '[(!:]' | first

        if $kind not-in $types {
            let advice = match $kind {
                ci => 'Use chore for CI work.'
                perf => 'Use feat or fix for performance work.'
                _ => $'Use ($types | str join ", ").'
            }
            $'($subject): GIT-02 ($advice)'
        } else if $subject =~ '^[^(\s!:]+(?:\([^)]*\))?!?: [A-Z]' {
            $subject + ': GIT-01 Start the summary in lower case.'
        }
    }
    | compact
}

# Validate recent commit syntax with Cocogitto and enforce repository-specific policies.
def "main commits" []: nothing -> nothing {
    let revisions = git rev-list --max-count=50 HEAD | lines
    let range = if ($revisions | length) == 50 { [$'($revisions | last)..HEAD'] } else { [] }

    checked 'cog check' cog check --ignore-merge-commits ...$range | ignore

    let types = open cog.toml | get commit_types | columns
    let subjects = git log --max-count=50 --format=%P%x09%s
    | lines
    | parse --regex '^(?<parents>[^\t]*)\t(?<subject>.*)$'
    | where parents !~ ' '
    | get subject
    let problems = commit-policy-problems $types ...$subjects

    if ($problems | is-not-empty) {
        fail $"The commits break repository policy:\n($problems | str join (char nl))"
    }
}

# Return the files that match the globs of a rule, outside the folders of tools, as paths from the repository root.
def files-of [rule: record]: nothing -> list<string> {
    $rule.globs
    | each {|pattern| glob $pattern --exclude $SKIPPED_FOLDERS }
    | flatten
    | each {|file| $file | path relative-to (pwd) }
    | uniq
    | where not (excluded $it ...$rule.except)
}

# Return the SQL files of the templates and the migrations, as paths from the repository root.
def sql-files []: nothing -> list<string> {
    $SQL
    | each {|pattern| glob $pattern }
    | flatten
    | each {|file| $file | path relative-to (pwd) }
    | uniq
}

# Write migration checksums after adding unapplied migrations.
def "main sums" []: nothing -> nothing {
    migration-files
    | each {|row| $'($row.hash)  ($row.file)' }
    | str join (char nl)
    | $in + (char nl)
    | save --force $SUMS
}

# Check source rules, SQL constraints, migration checksums and recent commit subjects.
def main []: nothing -> nothing {
    let files = $RULES
    | each {|rule| files-of $rule | each {|file| line-findings $rule $file ((open --raw $file | decode utf-8)) } | flatten }
    | flatten

    let sql = sql-files
    | each {|file|
        let text = open --raw $file | decode utf-8

        let sizes = function-problems $file $text ...$LONG_FUNCTIONS

        [...(header-problems $file $text) ...$sizes]
    }
    | flatten

    let test_templates = glob 'sync/tests/templates/**/*.sql' | each {|file| $file | path relative-to (pwd) }

    let inputs = [
        ...(sql-files)
        ...$test_templates
    ]
    | each {|file| input-problems $file ((open --raw $file | decode utf-8)) }
    | flatten

    let headers = $HEADED
    | each {|pattern| glob $pattern --exclude $SKIPPED_FOLDERS }
    | flatten
    | uniq
    | each {|file| file-header-problems ($file | path relative-to (pwd)) ((open --raw $file | decode utf-8)) }
    | flatten

    let helpers = glob $CHART_HELPERS
    | each {|file| define-problems ($file | path relative-to (pwd)) ((open --raw $file | decode utf-8)) }
    | flatten

    let migrations = migration-problems ((open --raw $SUMS | decode utf-8)) (migration-files)
    let reserved = reserved-project-problems ((open --raw console/ui/src/lib/projects.ts | decode utf-8)) ((open --raw provider/internal/provider/names.go | decode utf-8)) ((open --raw proxy/src/proxy/constants.lua | decode utf-8))
    main commits
    let found = [
        ...$files
        ...$sql
        ...$inputs
        ...$headers
        ...$helpers
        ...$migrations
        ...$reserved
    ]

    if ($found | is-not-empty) {
        fail $"The code breaks the style rules:\n($found | str join "\n")"
    }
}
