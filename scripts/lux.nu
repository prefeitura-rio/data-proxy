use lib.nu [fail parse-json read-text write-text]

# Return the digest of the rocks in the text of a lux.lock file, without the random order of the binaries.
def lock-digest [text: string]: nothing -> string {
    let lock = $text | parse-json

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
                dependencies: ($entry.rock.dependencies | sort)
            }
        }
    }
    | flatten
    | sort-by section id
    | to nuon
    | hash sha256
}

# Install the locked rocks and fail when `lux.lock` changed, ignoring Lux's random `binaries` order. Restore the committed file after a clean install, so repeated runs leave no diff. Run it from the repository root.
def main []: nothing -> nothing {
    try {
        cd proxy
    } catch {|err|
        fail $'Cannot enter proxy: ($err.msg)'
    }

    let lockfile = 'lux.lock'
    let original = read-text $lockfile
    let before = lock-digest $original

    lx --no-prompt true --lua-version jit sync

    if (lock-digest (read-text $lockfile)) != $before {
        fail $'Run lx add or lx update, then commit ($lockfile)'
    }

    $original | write-text $lockfile
}

# Run commands in the proxy folder, and restore `lux.lock` afterwards, because lux rewrites the file.
def with-lock [action: closure]: nothing -> nothing {
    try {
        cd proxy
    } catch {|err|
        fail $'Cannot enter proxy: ($err.msg)'
    }

    let lock = read-text lux.lock

    try { do $action } finally { $lock | write-text lux.lock }
}

# Lint the Lua code of the proxy with lux and the language server. Run it from the repository root.
def "main lint" []: nothing -> nothing {
    with-lock {||
        lx --lua-version jit lint
        lua-language-server --check . --configpath .luarc.json --logpath .lux/luals-log --checklevel=Hint
    }
}

# Run the unit tests of the proxy with lux. Run it from the repository root.
def "main test" []: nothing -> nothing {
    with-lock {|| lx --lua-version jit test }
}

# Format the Lua code of the proxy with lux. Run it from the repository root.
def "main format" []: nothing -> nothing {
    try {
        cd proxy
    } catch {|err|
        fail $'Cannot enter proxy: ($err.msg)'
    }

    lx --lua-version jit fmt
}
