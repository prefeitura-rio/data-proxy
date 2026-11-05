use std/assert

const SCRIPT = path self ../ci.nu

# Verify that the CI script parses. The linter does not check types, so a wrong argument type stays hidden until a run.
export def "test ci-script-parses" []: nothing -> nothing {
    let result = nu --no-config-file --commands $'source ($SCRIPT)' | complete

    assert equal $result.exit_code 0 $result.stderr
}
