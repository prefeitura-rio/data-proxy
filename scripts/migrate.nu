use lib.nu [check-result fail]


# Run one goose command on the application database or on the catalog database.
def main [
    command: string = up # The goose command, such as up, down, status, or version.
    --database: string = console # The database: console, or ducklake for the catalog database.
    --dir: path # The folder of the migrations. The default is the folder of the database.
]: nothing -> nothing {
    let url = match $database {
        console => $env.PG_DATABASE_URL
        ducklake => $env.DUCKLAKE_ADMIN_URL
        _ => (fail $'The database ($database) is neither console nor ducklake')
    }
    let migrations = $dir | default ($env.FILE_PWD | path dirname | path join migrations $database)
    let settings = {
        GOOSE_DRIVER: postgres
        GOOSE_DBSTRING: $url
        GOOSE_MIGRATION_DIR: $migrations
    }

    let result = with-env $settings { goose $command | complete }
    print ($result.stdout + $result.stderr)

    $result
    | check-result $'goose ($command)'
    | ignore
}
