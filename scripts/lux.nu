use lib.nu [fail   ]

# Return the digest of the rocks in the text of a lux.lock file, without the random order of the binaries.
def lock-digest [text: string]: nothing -> string {
    let lock = $text | from json

    [dependencies test_dependencies]
    | each {|section|
        $lock
        | get --optional $section
        | default {rocks: {}}
        | get rocks
        | transpose id rock
        | each {|entry|
            {
                section: $section
                id: $entry.id
                version: $entry.rock.version
                constraint: $entry.rock.constraint?
                hashes: $entry.rock.hashes
                dependencies: ($entry.rock.dependencies? | default [] | sort)
            }
        }
    }
    | flatten
    | sort-by section id
    | to nuon
    | hash sha256
}

# Install locked proxy dependencies and reject lockfile changes except binary ordering.
def main []: nothing -> nothing {
    try {
        cd proxy
    } catch {|err|
        fail $'Cannot enter proxy: ($err.msg)'
    }

    let lockfile = 'lux.lock'
    let original = open --raw $lockfile | decode utf-8
    let before = lock-digest $original

    lx --no-prompt true --lua-version jit sync

    if (lock-digest ((open --raw $lockfile | decode utf-8))) != $before {
        fail $'Run lx add or lx update, then commit ($lockfile)'
    }

    $original | save --force $lockfile
}

# Run commands in the proxy folder, and restore `lux.lock` afterwards, because lux rewrites the file.
def with-lock [action: closure]: nothing -> nothing {
    try {
        cd proxy
    } catch {|err|
        fail $'Cannot enter proxy: ($err.msg)'
    }

    let lock = open --raw lux.lock | decode utf-8

    try { do $action } finally { $lock | save --force lux.lock }
}

# Lint the Lua code of the proxy with lux and the language server.
def "main lint" []: nothing -> nothing {
    with-lock {||
        lx --lua-version jit lint
        lua-language-server --check . --configpath .luarc.json --logpath .lux/luals-log --checklevel=Hint
    }
}

# Run the unit tests of the proxy with lux.
def "main test" []: nothing -> nothing {
    with-lock {|| lx --lua-version jit test }
}

# Format proxy Lua files or report required changes without writing when --check is set.
def "main format" [
    --check # Report formatting changes without writing files.
]: nothing -> nothing {
    try {
        cd proxy
    } catch {|err|
        fail $'Cannot enter proxy: ($err.msg)'
    }

    if $check {
        stylua --check src tests
    } else {
        lx --lua-version jit fmt
    }
}

# Run the unit tests of the proxy with luacov and print the lines that no test runs.
def "main coverage" []: nothing -> nothing {
    with-lock {||
        lx --lua-version jit test -- --coverage

        let tree = pwd | path join .lux jit test_dependencies jit
        let source = glob ($tree | path join '*luacov*' src) | first
        with-env {LUA_PATH: $'($source)/?.lua;($source)/?/init.lua;;'} {
            ^($tree | path join bin unwrapped luacov)
        }

        let report = open --raw luacov.report.out | decode utf-8

        $report
        | lines
        | skip until {|line| $line | str starts-with 'Summary' }
        | each {|line| $line | str replace --regex '^.*/src/proxy/' 'proxy/' }
        | to text
        | print
    }
}
