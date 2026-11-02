use lib.nu [check-result]

# The migrations are plain SQL files in console/migrations/, and goose applies them in the table public.goose_db_version.
# They hold the data schema of the console: the configuration tables with their triggers and row policies, and the views
# that the console reads. They hold no grant, because the role of the token is a chart value, and the provision job
# grants the rights on the console schema.
#
# Everything else belongs to the chart, in helm/files/templates/postgres/: the functions that the console calls, the
# state tables, the schemas of the synced data, the pre-request function, the reload functions, the maintenance
# procedures and the roles. The order is: the chart SQL for the state tables, then `dbos migrate`, then `goose up`.
# scripts/db.nu doesn't run this order for the test database.
#
# Add a migration with `goose -dir console/migrations create <name> sql`, and write the Up and the Down part. Never
# edit a migration that a shared database has applied. Add a new one instead. `devenv test` runs `migrate up`, and
# `sync:lint:sql` checks the files with sqlfluff and `goose validate`.

# Run one goose command on the application database with the migrations of the console.
def main [
    command: string = up # The goose command, such as up, down, status, or version.
    --dir: path # The folder of the migrations. The default is the folder of the console.
]: nothing -> nothing {
    let migrations = $dir | default ($env.FILE_PWD | path dirname | path join console migrations)
    let settings = {
        GOOSE_DRIVER: postgres
        GOOSE_DBSTRING: $env.PG_DATABASE_URL
        GOOSE_MIGRATION_DIR: $migrations
    }

    let result = with-env $settings { goose $command | complete }
    print ($result.stdout + $result.stderr)

    $result
    | check-result $'goose ($command)'
    | ignore
}
