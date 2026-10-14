use lib.nu [fail]

# Run one goose command on the application database with the migrations of the console.
def main [
    command: string = up # The goose command, such as up, down, status, or version.
    --dir: path # The folder of the migrations. The default is the folder of the console.
]: nothing -> nothing {
    let migrations = $dir | default ($env.FILE_PWD | path dirname | path join sync console migrations)
    let settings = {
        GOOSE_DRIVER: postgres
        GOOSE_DBSTRING: $env.PG_DATABASE_URL
        GOOSE_MIGRATION_DIR: $migrations
    }

    let result = with-env $settings { goose $command | complete }
    print ($result.stdout + $result.stderr)

    if $result.exit_code != 0 {
        fail $'goose ($command) failed: ($result.stderr)' {
            command: main
            span: (metadata $command).span
        }
    }
}
