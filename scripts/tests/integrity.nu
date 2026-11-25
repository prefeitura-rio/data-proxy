use std/assert

# Exercise repository checks against isolated source and migration fixtures.
def main []: nothing -> nothing {
    let root = $env.FILE_PWD | path dirname --num-levels 2
    for case in [
        {id: ordinary_code, mutation: none, success: true}
        {id: migration_changed, mutation: migration, success: false}
        {id: reserved_names_differ, mutation: reserved, success: false}
        {id: malformed_metadata, mutation: metadata, success: false}
        {id: missing_template_input, mutation: input, success: false}
        {id: ambient_nix_input, mutation: nix, success: false}
        {id: unsafe_identifier_quote, mutation: quoting, success: false}
        {id: parser_suppressed, mutation: parsing, success: false}
        {id: unconfigured_commit, mutation: commit, success: false}
    ] {
        let folder = mktemp --directory --tmpdir 'data-proxy-integrity.XXXXXXXX'
        try {
            for directory in [migrations/console migrations/ducklake sync/src/sync/templates/postgres sync/src/sync/templates/duckdb sync/tests/templates console/ui/src/lib provider/internal/provider proxy/src/proxy scripts console/notebooks helm/templates helm/files/scripts helm/files/templates/postgres] {
                mkdir ($folder | path join $directory)
            }
            for file in [console/ui/src/lib/projects.ts provider/internal/provider/names.go proxy/src/proxy/constants.lua] {
                cp ($root | path join $file) ($folder | path join $file)
            }
            cp ($root | path join cog.toml) ($folder | path join cog.toml)
            '{{- define "fixture" -}}value{{- end -}}' | save ($folder | path join helm templates _fixture.tpl)
            let long_body = 1..70 | each { '    PERFORM 1;' } | str join (char nl)
            ("CREATE FUNCTION fixture() RETURNS void LANGUAGE plpgsql AS $$\nBEGIN\n" + $long_body + "\nEND;\n$$;") | save ($folder | path join sync src sync templates postgres cohesive.sql)
            let migration = $folder | path join migrations console 00001_fixture.sql
            'SELECT 1;' | save $migration
            let hash = open --raw $migration | hash sha256
            $'($hash)  migrations/console/00001_fixture.sql(char nl)' | save ($folder | path join migrations SHA256SUMS)
            'rec { value = 1; }' | save ($folder | path join example.nix)
            'local VALUE = 1
assert(VALUE == 1)
' | save ($folder | path join proxy src proxy example.lua)
            '# Return the fixture input.
def example [value: int]: nothing -> int {
    # Explain a domain constraint at its point of use.
    $value
}
' | save ($folder | path join scripts example.nu)
            let template = $folder | path join sync src sync templates postgres fixture.sql
            '{#
{"kind":"template","inputs":{"value":"An integer."}}
#}
SELECT {{ value }};' | save $template

            match $case.mutation {
                migration => { 'SELECT 2;' | save --force $migration }
                reserved => { 'export const RESERVED_PROJECTS = [];' | save --force ($folder | path join console ui src lib projects.ts) }
                metadata => { '{#
{broken}
#}
SELECT 1;' | save --force $template }
                input => { '{#
{"kind":"template","inputs":{}}
#}
SELECT {{ missing }};' | save --force $template }
                nix => { 'import <nixpkgs> {}' | save --force ($folder | path join example.nix) }
                quoting => { r#'SELECT '"' || replace(name, '"', '""');'# | save --force $template }
                parsing => { "-- noqa: disable=PRS\nSELECT 1;" | save --force $template }
                _ => { }
            }
            git -C $folder init --quiet
            git -C $folder config user.name 'Integrity test'
            git -C $folder config user.email 'integrity@example.org'
            git -C $folder config commit.gpgsign false
            git -C $folder config core.hooksPath /dev/null
            git -C $folder add .
            let message = if $case.mutation == commit { 'ci: Fixture behavior' } else { 'fix: Fixture behavior' }
            git -C $folder commit --quiet --message $message
            let result = with-env {} {
                cd $folder
                nu --no-config-file ($root | path join scripts rules.nu) | complete
            }
            assert equal ($result.exit_code == 0) $case.success $'($case.id): ($result.stderr)'
        } finally {
            rm --recursive --force $folder
        }
    }
}
