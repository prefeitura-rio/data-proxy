use ./lib.nu [wait-for-sql]

# Wait until the SQL in PROBE answers true. PROBE_NAME says what the probe waits for. PROBE_URL names the database, and the default is the one of the PG variables.
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
