use lib.nu [check-result fail]

# The migrations are plain SQL files in migrations/console/ for the application database and in migrations/ducklake/ for the
# catalog database, and goose applies them in the table public.goose_db_version of each database.
# They hold what changes only when a developer changes it: the extensions, the schemas, the tables, the views, the
# functions, the triggers and the grants to the request roles. A grant takes the name of a role from the environment
# through `-- +goose ENVSUB ON`, between that line and `-- +goose ENVSUB OFF`. Keep a $$ body outside that region,
# because goose changes a dollar sign inside it, and quote the name, so that a name that is not set stops the run.
#
# Everything that depends on a secret, on rbac.toml, on the schemas that the console lists or on a sync run stays in the
# chart, in helm/files/templates/postgres/ and in the sync templates. The order is: the request roles, then `dbos migrate`,
# then `goose up` on both databases, then the rules of rbac.toml and the schemas. scripts/db.nu does the same for the
# test databases.
#
# Add a migration with `goose -dir migrations/console create <name> sql`, and write the Up and the Down part. Never edit a
# migration that a shared database has applied. Add a new one instead, and let a migration that adds a console table, view
# or function grant it. `nu scripts/rules.nu sums` writes the hashes of the new files. `sync:lint:sql` checks the files
# with sqlfluff and `goose validate`.

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
