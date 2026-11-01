use lib.nu [fail read-text]

const SKIPPED_FOLDERS = [
    '**/node_modules/**'
    '**/.devenv/**'
    '**/.venv/**'
    '**/.lux/**'
    '**/__pycache__/**'
]

const COMMIT_TYPES = '^(feat|fix|refactor|test|docs|chore|revert)(\([a-z0-9,_ -]+\))?!?: '

const HEADER = '(?s)\{#\n(\{.*?\})\n#\}'

const HEADER_WORDS = 40

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
    'console/migrations/*.sql'
]

const PARSER_DISABLED = [
    console/migrations/
    sync/src/sync/templates/duckdb/
    sync/src/sync/templates/postgres/create_table_changes_function.sql
    sync/src/sync/templates/postgres/delete_removed_state.sql
    sync/src/sync/templates/postgres/views/create_function.sql
    sync/tests/templates/duckdb/publish_people_snapshots.sql
]

const NU_LIBS = [
    scripts/lib.nu
    helm/files/scripts/lib.nu
    scripts/tests/
    helm/files/scripts/tests/
]

export const RULES = [
    [id globs pattern skip except message];
    [LUA-47 $LUA '\bassert\(' '--' [] "Check the value and raise an error with a message."]
    [LUA-64 $LUA '---@diagnostic disable' '' [] "Fix the cause and don't disable a diagnostic."]
    [LUA-09 $LUA 'os\.getenv' '--' [proxy/src/proxy/main.lua] "Read a setting only from the config table."]
    [LUA-12 $LUA '^local [A-Z][A-Z_0-9]+ =' '' [proxy/src/proxy/constants.lua] "Define the constant in constants.lua."]
    [NIX-18 $NIX '\brec \{|= rec ' '#' [] "Bind the shared names in a let block."]
    [NIX-20 $NIX '\bimport <[a-z]+>' '#' [] "Don't use a lookup path."]
    [NU-15 $NU '\berror make\b' '#' [...$NU_LIBS console/notebooks/build.nu] "Stop with fail from lib.nu."]
    [NU-16 $NU '(^|[|={(])\s*(open|save|mkdir|rm|cp|mv)\s' '#' [...$NU_LIBS console/notebooks/build.nu] "Use the file helpers of lib.nu."]
    [SQL-04 $SQL '^\s+\w+\s+(timestamp(?! with)|(big)?serial)\b' '--' [] "Use timestamptz and bigint GENERATED ALWAYS AS IDENTITY for a column."]
    [SQL-24 $SQL r#'"' \|\| replace\('# '--' [sync/src/sync/templates/postgres/views/routing/quoting.sql] "Quote a DuckDB identifier with quote_column()."]
    [SQL-29 [...$SQL 'sync/tests/templates/**/*.sql'] '^-- noqa: disable=[A-Z0-9,]*PRS' '' $PARSER_DISABLED "Make SQLFluff parse the template, or add it to the allowed list with a reason."]
    [K6-13 ['k6/**/*.js'] '\bconsole\.(log|warn|error|info|debug)\b' '//' [k6/lib/log.js] "Log with info and warn from lib/log.js."]
    [GEN-11 $NU '^\s{4,}#\s' '' [] "Write no comment inside a function body."]
    [GEN-11 ['console/ui/src/**/*.ts'] '^\t{2,}(//|/\*)' '' [] "Write no comment inside a function body."]
]

# Return the findings of one rule in the text of one file, as `file:line: ID message`. A line that starts with the skip prefix is a comment.
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

    if $description == null { 0 } else { $description | split row --regex '\s+' | length }
}

# Return a problem for each header of a template whose description has more than 40 words.
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

# Return true when the path starts with one of the files or folders that the rule allows.
export def excluded [file: string, ...except: string]: nothing -> bool {
    $except | any {|head| $file | str starts-with $head }
}

# Return the problems of the commit subjects that break the Conventional Commits rules of the guide.
export def commit-problems [...subjects: string]: nothing -> list<string> {
    $subjects
    | where not ($it | str starts-with 'Merge ')
    | each {|subject|
        if $subject =~ '^(ci|perf)(\(|!|:)' {
            let advice = if ($subject | str starts-with 'ci') { 'Use chore for CI work.' } else { 'Use feat or fix for performance work.' }
            $'($subject): GIT-03 ($advice)'
        } else if $subject !~ $COMMIT_TYPES {
            $'($subject): GIT-02 Use feat, fix, refactor, test, docs, chore or revert.'
        } else if $subject =~ $'($COMMIT_TYPES)[A-Z]' {
            $subject + ': GIT-01 Write type(scope): summary in lower case.'
        }
    }
    | compact
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

# Check the code against the rules that a pattern can check, and the last 50 commit subjects against the commit rules.
def main []: nothing -> nothing {
    let files = $RULES
    | each {|rule| files-of $rule | each {|file| line-findings $rule $file (read-text $file) } | flatten }
    | flatten

    let headers = $SQL
    | each {|pattern| glob $pattern }
    | flatten
    | each {|file| $file | path relative-to (pwd) }
    | each {|file| header-problems $file (read-text $file) }
    | flatten

    let commits = commit-problems ...(git log --max-count=50 --format=%s | lines)
    let found = [...$files ...$headers ...$commits]

    if ($found | is-not-empty) {
        fail $"The code breaks the style rules:\n($found | str join "\n")"
    }
}
