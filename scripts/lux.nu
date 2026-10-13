use lib.nu [fail]

# Install the locked rocks and fail when `lux.lock` changed, ignoring Lux's random `binaries` order. Run it from the repository root.
def main []: nothing -> nothing {
    cd proxy

    let lockfile = 'lux.lock'

    let digest = {||
        let lock = try {
            open --raw $lockfile | from json
        } catch {|err| fail $'Could not read ($lockfile): ($err.msg)' {
                command: main
                span: (metadata $lockfile).span
            } }

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

    let before = do $digest

    lx --no-prompt true --lua-version jit sync

    if (do $digest) != $before {
        fail $'Run lx add or lx update, then commit ($lockfile)' {
            command: main
            span: (metadata $lockfile).span
        }
    }
}
