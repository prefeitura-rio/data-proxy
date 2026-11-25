use std/assert
use ../lib.nu [render-template]
use ../lib/release.nu [images-changed]
use ../ci/common.nu [write-output write-summary]

# Verify CI outputs preserve Unicode, newlines and existing contents.
def output-files [folder: path]: nothing -> nothing {
    let output = $folder | path join 'job output.txt'
    let summary = $folder | path join 'job summary.txt'
    "existing=true\n" | save $output

    with-env {CI_OUTPUT_FILE: $output, GITHUB_STEP_SUMMARY: $summary} {
        write-output version '0.2.0'
        write-output label 'São Paulo'
        write-summary 'First line'
        write-summary 'Second line'
    }

    assert equal (open --raw $output) "existing=true\nversion=0.2.0\nlabel=São Paulo\n"
    assert equal (open --raw $summary) "First line\nSecond line\n"
    assert error { with-env {CI_OUTPUT_FILE: $folder} { write-output invalid target } }
}

# Verify template rendering preserves text and removes temporary context files after success or failure.
def template-files [folder: path]: nothing -> nothing {
    let template = $folder | path join 'template with spaces.j2'
    let temporary = $folder | path join 'temporary contexts'
    mkdir $temporary
    "Hello {{ name }}\n" | save $template

    with-env {TMPDIR: $temporary} {
        assert equal (render-template $template {name: 'São Paulo'}) "Hello São Paulo\n"
        assert equal (glob ($temporary | path join '*')) []
        assert error { render-template $template {} }
        assert equal (glob ($temporary | path join '*')) []
    }
}

# Run isolated setup regression tests and remove every fixture afterward.
def main []: nothing -> nothing {
    let folder = mktemp --directory --tmpdir 'data-proxy-setup.XXXXXXXX'
    let tests = [
        {id: image_selection, run: {||
            for case in [
                {id: postgres_recipe, files: [Containerfile.postgres], force: false, expected: [postgres]}
                {id: unrelated_file, files: [docs/index.md], force: false, expected: []}
                {id: obsolete_recipe, files: [Containerfile.pg], force: false, expected: []}
                {id: forced_build, files: [], force: true, expected: [sync proxy jobs postgres console]}
            ] {
                assert equal (images-changed $case.files $case.force | get id) $case.expected $case.id
            }
        }}
        {id: output_files, run: {|| output-files $folder }}
        {id: template_files, run: {|| template-files $folder }}
    ]

    try {
        for test in $tests {
            do $test.run
            print $'PASS ($test.id)'
        }
    } finally {
        rm --recursive --force $folder
    }
}
