use ./lib.nu [wait-for-sql]

# Wait until PROBE succeeds against the configured PostgreSQL connection.
def main []: nothing -> nothing {
    let name = $env.PROBE_NAME
    let probe = $env.PROBE
    let url = $env.PROBE_URL?

    if $url == null {
        wait-for-sql $name $probe
    } else {
        wait-for-sql $name $probe --dsn $url
    }
}
