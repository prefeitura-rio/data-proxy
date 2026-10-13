# Run nu-lint over every Nushell script in the repository.
def main []: nothing -> nothing {
    let files = [helm/files scripts]
    | each {|directory| glob $'($directory)/**/*.nu' }
    | flatten

    $files | each {|file| nu-lint --config .nu-lint.toml $file } | ignore
}
