use std/assert

# Verify advisory Nu findings never conceal syntax, configuration or process failures.
def nu-findings []: nothing -> nothing {
    let root = $env.FILE_PWD | path dirname --num-levels 2
    for case in [
        {id: suggestion, mode: hint, syntax: true, success: true}
        {id: warning, mode: warning, syntax: true, success: true}
        {id: error, mode: error, syntax: true, success: false}
        {id: invalid_config, mode: config, syntax: true, success: false}
        {id: missing_tool, mode: missing, syntax: true, success: false}
        {id: invalid_syntax, mode: hint, syntax: false, success: false}
    ] {
        let folder = mktemp --directory --tmpdir 'data-proxy-checks.XXXXXXXX'
        try {
            for path in [helm/files scripts k3d/manifests/files console/notebooks bin] { mkdir ($folder | path join $path) }
            cp ($root | path join .nu-lint.toml) ($folder | path join .nu-lint.toml)
            (if $case.syntax { 'let value = 1' } else { 'def main [' }) | save ($folder | path join scripts fixture.nu)
            let stub = $folder | path join stub.nu
            '# Emit one controlled linter result.
def --wrapped main [...arguments: string]: nothing -> nothing {
    match $env.TEST_MODE {
        config => { exit 2 }
        missing => { exit 127 }
        _ => { print ($"scripts/fixture.nu:1:1: ($env.TEST_MODE)" + "(fixture): example finding") }
    }
}
' | save $stub
            $'#!/bin/sh(char nl)exec ($nu.current-exe | to json --raw) --no-config-file ($stub | to json --raw) "$@"(char nl)' | save ($folder | path join bin nu-lint)
            chmod +x ($folder | path join bin nu-lint)
            let result = with-env {PATH: ($env.PATH | prepend ($folder | path join bin)), TEST_MODE: $case.mode} {
                cd $folder
                nu --no-config-file ($root | path join scripts nu-lint.nu) | complete
            }
            assert equal ($result.exit_code == 0) $case.success $'($case.id): ($result.stderr)'
        } finally {
            rm --recursive --force $folder
        }
    }
}

# Verify native Vale advisory mode preserves configuration failures.
def prose-findings []: nothing -> nothing {
    let folder = mktemp --directory --tmpdir 'data-proxy-prose.XXXXXXXX'
    try {
        let styles = $folder | path join styles Fixture
        mkdir $styles
        'extends: existence
message: Advisory wording
level: error
tokens:
  - example
' | save ($styles | path join Advice.yml)
        'StylesPath = styles
[*.md]
BasedOnStyles = Fixture
' | save ($folder | path join .vale.ini)
        'example' | save ($folder | path join fixture.md)
        let blocking = vale --config ($folder | path join .vale.ini) ($folder | path join fixture.md) | complete
        assert ($blocking.exit_code != 0)
        let advisory = vale --no-exit --config ($folder | path join .vale.ini) ($folder | path join fixture.md) | complete
        assert equal $advisory.exit_code 0 $advisory.stderr
        'StylesPath = styles
[*.md]
BasedOnStyles = MissingStyle
' | save --force ($folder | path join .vale.ini)
        let invalid = vale --no-exit --config ($folder | path join .vale.ini) ($folder | path join fixture.md) | complete
        assert ($invalid.exit_code != 0)
    } finally {
        rm --recursive --force $folder
    }
}

# Run process-contract checks without invoking application services.
def main []: nothing -> nothing {
    nu-findings
    prose-findings
}
