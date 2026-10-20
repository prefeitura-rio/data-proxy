use lib.nu [fail]

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
    cd proxy

    let lockfile = 'lux.lock'
    let original = open --raw $lockfile
    let before = lock-digest $original

    lx --no-prompt true --lua-version jit sync

    if (lock-digest (open --raw $lockfile)) != $before {
        fail $'Run lx add or lx update, then commit ($lockfile)'
    }

    $original | save --force --raw $lockfile
}

# Run commands in the proxy folder, and restore `lux.lock` afterwards, because lux rewrites the file.
def with-lock [action: closure]: nothing -> nothing {
    cd proxy

    let lock = open --raw lux.lock

    try { do $action } finally { $lock | save --force --raw lux.lock }
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
    cd proxy

    lx --lua-version jit fmt
}
