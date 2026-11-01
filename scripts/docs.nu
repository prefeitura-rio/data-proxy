use lib.nu [check-result fail read-data read-text write-text]

const START = '<!-- roles-matrix:start -->'
const END = '<!-- roles-matrix:end -->'
const PAGE = 'docs/security/roles.md'
const RULES = 'helm/files/config/rbac.toml'
const DOCS = 'docs'
const NAV = 'docs/.nav.json'
const MKDOCS_NAV = 'mkdocs.nav.yml'
const NAV_START = '<!-- nav:start -->'
const NAV_END = '<!-- nav:end -->'

# Return the permissions of a role, including the permissions of the roles that it inherits.
export def closure [roles: list<record>, name: string]: nothing -> list<string> {
    let role = $roles | where name == $name | first

    let inherited = $role
    | get --optional inherits
    | default []
    | each {|parent| closure $roles $parent }
    | flatten

    $role.permissions | append $inherited | uniq
}

# Return every permission name of the rules, as `<action>_<object>`, in file order.
export def permission-names [rules: record]: nothing -> list<string> {
    $rules.permissions
    | items {|object, body|
        $body | reject description | columns | each {|action| $'($action)_($object)' }
    }
    | flatten
}

# Render the Markdown table of the roles and their permissions, with one row for each permission.
export def matrix [rules: record]: nothing -> string {
    let granted = $rules.roles
    | each {|role| {name: $role.name, permissions: (closure $rules.roles $role.name)} }

    permission-names $rules
    | each {|permission|
        $granted
        | reduce --fold {Permission: $'`($permission)`'} {|role, row|
            $row | insert $'`($role.name)`' (if $permission in $role.permissions { 'yes' } else { '' })
        }
    }
    | to md --pretty
}

# Replace the table between the markers of a page with the rendered table.
export def splice [page: string, table: string]: nothing -> string {
    let pattern = ['(?s)(' $START '\n).*?(' $END ')'] | str join

    $page | str replace --regex $pattern $"${1}($table)\n${2}"
}

# Return the pages of the navigation tree in reading order, each with the page of its parent.
# The parent of the home page is an empty string.
export def flatten-nav [node: record, parent: string = '']: nothing -> list<record> {
    let children = $node
    | get --optional children
    | default []
    | each {|child| flatten-nav $child $node.page }
    | flatten

    [
        {page: $node.page, parent: $parent}
        ...$children
    ]
}

# Return the first heading of a page.
def title [root: path, page: path]: nothing -> string {
    read-text ($root | path join $page)
    | lines
    | where $it =~ '^# '
    | first
    | str substring 2..
}

# Return the MkDocs item of a node of the navigation tree.
# A page without children is its path. A page with children is a section that starts with the page.
export def mkdocs-item [node: record, root: path]: nothing -> oneof<string, record> {
    let children = $node | get --optional children | default []

    if ($children | is-empty) {
        return $node.page
    }

    {
        (title $root $node.page): [
            $node.page
            ...($children | each {|child| mkdocs-item $child $root })
        ]
    }
}

# Return the path components of a path, or an empty list for an empty path.
def components [path: string]: nothing -> list<string> {
    if $path == '' {
        []
    } else {
        $path | path split
    }
}

# Return the relative link from one page to another, both as paths below the root.
def relative-link [from: string, to: string]: nothing -> string {
    let base = components ($from | path dirname)

    let target = components $to

    let common = $base
    | zip $target
    | take while {|pair| ($pair | uniq | length) == 1 }
    | length

    $base | skip $common | each { '..' } | append ($target | skip $common) | str join '/'
}

# Return a Markdown link.
def md-link [label: string, target: string]: nothing -> string {
    ['[' $label '](' $target ')'] | str join
}

# Return the problems of the navigation: a file that the tree lacks, a page that has no file,
# and a page that its parent doesn't link.
export def problems [entries: list<record>, root: path]: nothing -> list<string> {
    let pages = $entries | get page

    let base = $root | path expand

    let lost = glob ($base | path join '**' '*.md')
    | each {|file| $file | path relative-to $base }
    | where $it not-in $pages
    | sort
    | each {|file| $'not in the navigation: ($file)' }

    let missing = $pages
    | where not ($root | path join $it | path exists)
    | each {|page| $'no such file: ($page)' }

    let unlinked = $entries
    | where parent != ''
    | each {|entry|
        let parent_file = $root | path join $entry.parent

        let target = ['](' (relative-link $entry.parent $entry.page)] | str join

        if ($parent_file | path exists) and (read-text $parent_file | str index-of $target) < 0 {
            $"($entry.parent) doesn't link ($entry.page)"
        }
    }
    | compact

    [...$lost ...$missing ...$unlinked]
}

# Return the navigation line of a page: previous, next, index and home.
export def footer [root: path, entries: list<record>, page: string]: nothing -> string {
    let pages = $entries | get page

    let position = $pages | enumerate | where item == $page | get index | first

    let parent = $entries | where page == $page | get parent | first

    [
        (if $position > 0 {
            let previous = $pages | get --optional ($position - 1)

            md-link $'Previous: (title $root $previous)' (relative-link $page $previous)
        })
        (if $position < (($pages | length) - 1) {
            let next = $pages | get --optional ($position + 1)

            md-link $'Next: (title $root $next)' (relative-link $page $next)
        })
        (
            if $parent not-in ['' index.md] { md-link Index (relative-link $page $parent) }
        )
        (if $page != index.md { md-link Home (relative-link $page index.md) })
    ]
    | compact
    | str join ' | '
}

# Add the navigation block at the end of a page, or replace the block that the page has.
export def render-page [text: string, links: string]: nothing -> string {
    let block = $"($NAV_START)\n---\n\n($links)\n($NAV_END)"

    let pattern = ['(?s)' $NAV_START '.*?' $NAV_END] | str join

    if ($text | str index-of $NAV_START) >= 0 {
        $text | str replace --regex $pattern $block
    } else {
        $"($text | str trim --right)\n\n($block)\n"
    }
}

# Return the pages whose text differs from the text with a current navigation block.
def stale-pages [entries: list<record>, root: path]: nothing -> list<string> {
    $entries
    | get page
    | each {|page|
        let text = read-text ($root | path join $page)

        if (render-page $text (footer $root $entries $page)) != $text { $page }
    }
    | compact
}

# Return the page with the table of the rules.
def render []: nothing -> string {
    splice (read-text $PAGE) (matrix (read-data $RULES))
}

# Write the role and permission table in the Roles page.
def "main write" []: nothing -> nothing {
    render | write-text $PAGE
}

# Fail when the Roles page has an old role and permission table.
def "main check" []: nothing -> nothing {
    if (render) != (read-text $PAGE) {
        fail $'($PAGE) has an old table: run `nu scripts/docs.nu write`'
    }
}

# Return the navigation entries of the docs, and fail when the tree and the files disagree.
def load-nav []: nothing -> list<record> {
    let entries = flatten-nav (read-data $NAV)

    let found = problems $entries $DOCS

    if ($found | is-not-empty) {
        fail $"The navigation in ($NAV) has problems:\n($found | str join "\n")"
    }

    $entries
}

# Write the navigation block of every page of the docs and the MkDocs navigation, in the order of docs/.nav.json.
def "main nav write" []: nothing -> nothing {
    let entries = load-nav
    let tree = read-data $NAV
    let sections = $tree | get children | each {|child| mkdocs-item $child $DOCS }

    {
        nav: [$tree.page ...$sections]
    } | to yaml | write-text $MKDOCS_NAV

    for page in (stale-pages $entries $DOCS) {
        let file = $DOCS | path join $page

        render-page (read-text $file) (footer $DOCS $entries $page) | write-text $file
    }
}

# Fail when a page has an old navigation block, or when docs/.nav.json and the files disagree.
def "main nav check" []: nothing -> nothing {
    let old = stale-pages (load-nav) $DOCS

    if ($old | is-not-empty) {
        fail $'These pages have an old navigation block, run `nu scripts/docs.nu nav write`: ($old | str join ", ")'
    }
}

# Return the heading anchor that MkDocs makes: lowercase, no punctuation, a hyphen for each space.
export def slug [heading: string]: nothing -> string {
    $heading
    | str lowercase
    | str trim
    | str replace --all --regex '[^\w\- ]' ''
    | split row ' '
    | str join '-'
}

# Return the lines of a text that are not in a code fence, with their line numbers.
def prose [text: string]: nothing -> list<record<number: int, text: string>> {
    $text
    | lines
    | enumerate
    | reduce --fold {fenced: false, rows: []} {|row, state|
        if ($row.item | str starts-with '```') {
            $state | update fenced { not $in }
        } else if $state.fenced {
            $state
        } else {
            $state | update rows { append {number: ($row.index + 1), text: $row.item} }
        }
    }
    | get rows
}

# Return the anchors of the headings of a text.
export def anchors [text: string]: nothing -> list<string> {
    prose $text
    | get text
    | parse --regex '^#{1,6} (?<title>.+)$'
    | each {|heading| slug $heading.title }
}

# Return the targets of the Markdown links of a text, with their line numbers. Code is not a link.
export def links-of [text: string]: nothing -> list<record<number: int, target: string>> {
    prose $text
    | each {|row|
        $row.text
        | str replace --all --regex '`[^`]*`' ''
        | parse --regex '\[[^\]]*\]\((?<target>[^)\s]+)\)'
        | each {|link| {number: $row.number, target: $link.target} }
    }
    | flatten
}

# Return the broken links of the files: a link to a file that is not there, or to a heading that the page lacks.
# A link that starts with a scheme, such as https:, is not checked.
export def link-problems [...files: path]: nothing -> list<string> {
    $files
    | each {|file|
        links-of (read-text $file)
        | where target !~ '^[a-z]+:'
        | each {|link|
            let hash = $link.target | str index-of '#'

            let path = if $hash < 0 { $link.target } else { $link.target | str substring 0..<$hash }

            let fragment = if $hash < 0 { '' } else { $link.target | str substring ($hash + 1).. }

            let target = if $path == '' { $file } else { $file | path dirname | path join $path | path expand }

            let where = $'($file):($link.number): ($link.target)'

            if not ($target | path exists) {
                $'($where): no such file'
            } else if $fragment != '' and ($target | str ends-with .md) and $fragment not-in (anchors (read-text $target)) {
                $'($where): no such heading'
            }
        }
        | compact
    }
    | flatten
}

# Fail when a link of the docs, the README or the style guide points to a missing file or heading.
def "main links" []: nothing -> nothing {
    let files = [...(glob $'($DOCS)/**/*.md') README.md STYLEGUIDE.md]

    let found = link-problems ...$files

    if ($found | is-not-empty) {
        fail $"The docs have broken links:\n($found | str join "\n")"
    }
}

# Return the task names that follow `tasks run`. The first word is a task or a prefix. A later word needs a colon.
def task-names [rest: string]: nothing -> list<string> {
    $rest
    | split row --regex '\s+'
    | where $it != ''
    | each {|word| $word | str replace --regex '[.,;)]+$' '' }
    | enumerate
    | take while {|word| $word.item =~ '^[a-z][a-z0-9-]*(:[a-z0-9-]+)*$' and ($word.index == 0 or $word.item =~ ':') }
    | get item
}

# Return the devenv commands of a text, with the line number, the profile, the verb and the task names of `tasks run`.
# A command without a profile uses the profile default. A placeholder such as `<name>` is not a command.
export def commands-of [text: string]: nothing -> table<line: int, profile: string, verb: string, tasks: list<string>> {
    $text
    | lines
    | enumerate
    | each {|row|
        $row.item
        | parse --regex 'devenv(?: --profile (?<profile>[a-z][a-z-]*))? (?<verb>tasks run|tasks list|test|up|shell|build|eval|processes)\b(?<rest>[^`]*)'
        | each {|found|
            {
                line: ($row.index + 1)
                profile: (if ($found.profile | is-empty) { 'default' } else { $found.profile })
                verb: $found.verb
                tasks: (if $found.verb == 'tasks run' { task-names $found.rest } else { [] })
            }
        }
    }
    | flatten
}

# Return the wrong commands: a profile that the Nix files do not define, or a task that the profile does not have.
# A task name is valid when it is a task or the start of one, such as `repo` or `sync:test`.
export def command-problems [commands: table, profiles: list<string>, tasks_of: closure]: nothing -> list<string> {
    $commands
    | each {|command|
        let where = $'($command.file):($command.line)'

        if $command.profile not-in $profiles {
            $'($where): no profile ($command.profile)'
        } else {
            let known = do $tasks_of $command.profile

            $command.tasks
            | each {|task|
                let exists = $known | any {|name| $name == $task or ($name | str starts-with $'($task):') }

                if not $exists {
                    $'($where): no task ($task) in the profile ($command.profile)'
                }
            }
            | compact
        }
    }
    | flatten
}

# Return the names of the profiles that the Nix files define.
def known-profiles []: nothing -> list<string> {
    [...(glob nix/profiles/*.nix) ...(glob */devenv.nix) devenv.nix]
    | each {|file| read-text $file | parse --regex 'profiles\.(?<name>[a-z][a-z-]*) =' }
    | flatten
    | get name
    | uniq
}

# Return the task names that `devenv tasks list` shows for a profile.
def listed-tasks [profile: string]: nothing -> list<string> {
    devenv --profile $profile tasks list
    | complete
    | check-result $'the task list of the profile ($profile)'
    | parse --regex '(?<name>[a-z0-9]+(?::[a-z0-9-]+)+)'
    | get name
    | uniq
}

# Fail when a devenv command of the docs, the README, the style guide or AGENTS.md names a profile or a task that does not exist.
def "main cmd" []: nothing -> nothing {
    let files = [...(glob $'($DOCS)/**/*.md') ...(glob '{README,AGENTS,STYLEGUIDE}.md')]

    let commands = $files
    | each {|file| commands-of (read-text $file) | each {|command| $command | insert file $file } }
    | flatten

    let profiles = known-profiles

    let lists = $commands
    | get profile
    | uniq
    | where $it in $profiles
    | each {|profile| {profile: $profile, tasks: (listed-tasks $profile)} }

    let tasks_of = {|profile| $lists | where profile == $profile | first | get tasks }

    let found = command-problems $commands $profiles $tasks_of

    if ($found | is-not-empty) {
        fail $"The docs have wrong devenv commands:\n($found | str join "\n")"
    }
}

# Return the ids of the processes that listen on a port. The command lsof exits with 1 when there are none.
def listeners [port: int]: nothing -> list<int> {
    let result = lsof -t -i $'tcp:($port)' -sTCP:LISTEN | complete

    if $result.exit_code > 1 {
        fail $'lsof failed: ($result.stderr)'
    }

    $result.stdout | lines | into int
}

# Stop the MkDocs servers that listen on a port, and fail when another program uses the port.
def free-port [port: int]: nothing -> nothing {
    for pid in (listeners $port) {
        let found = ps --long | where pid == $pid

        if ($found | is-empty) {
            continue
        }

        if $found.command.0 !~ mkdocs {
            fail $'Port ($port) is in use by process ($pid), which is not MkDocs. Set DOCS_PORT to use another port.'
        }

        kill $pid
    }

    while (listeners $port | is-not-empty) {
        sleep 100ms
    }
}

# Stop an old MkDocs server on the port, then serve the docs and open them in the browser.
def "main serve" [
    --opener: string = xdg-open # The program that opens the address, `open` on macOS.
]: nothing -> nothing {
    let port = $env.DOCS_PORT? | default 18200 | into int

    free-port $port

    let address = $'127.0.0.1:($port)'

    job spawn {
        sleep 2sec
        ^$opener $'http://($address)'
    }

    mkdocs serve --dev-addr $address
}

def main []: nothing -> nothing {
    help main
}
