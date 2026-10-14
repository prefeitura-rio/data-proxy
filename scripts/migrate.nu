use lib.nu [fail]

const SCHEMA = 'data_proxy'

# Return the connection string with the console schema first on the search path, so that goose keeps its version table there.
def goose-url [url: string]: nothing -> string {
    let parsed = $url | url parse
    let query = [$parsed.query $'search_path=($SCHEMA)'] | where {|part| $part != '' } | str join '&'

    $parsed | reject params | update query $query | url join
}

# Run one goose command on the application database with the migrations of the console.
def main [
    command: string = up # The goose command, such as up, down, status, or version.
    --dir: path # The folder of the migrations. The default is the folder of the console.
]: nothing -> nothing {
    let url = $env.PG_DATABASE_URL
    let migrations = $dir | default ($env.FILE_PWD | path dirname | path join sync console migrations)

    let schema = psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 $url --command $'CREATE SCHEMA IF NOT EXISTS ($SCHEMA)' | complete
    if $schema.exit_code != 0 {
        fail $'Could not create the schema ($SCHEMA): ($schema.stderr)' {
            command: main
            span: (metadata $url).span
        }
    }

    let result = goose -dir $migrations postgres (goose-url $url) $command | complete
    print ($result.stdout + $result.stderr)

    if $result.exit_code != 0 {
        fail $'goose ($command) failed: ($result.stderr)' {
            command: main
            span: (metadata $command).span
        }
    }
}
