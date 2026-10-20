use lib.nu [fail poll]

# Wait until a URL answers, or until the timeout. A timeout of 0 sec makes one attempt.
def "main url" [
    url: string # The URL to call.
    --timeout: duration = 10min # How long to try.
    --allow-errors # Count any HTTP answer as up, also a 403 or a 404.
]: nothing -> nothing {
    let attempts = ($timeout // 1sec) + 1
    let up = poll {||
        try {
            http get --allow-errors=$allow_errors --max-time 2sec $url | ignore
            true
        } catch {
            false
        }
    } {interval: 1sec, max_attempts: $attempts}

    if not $up {
        fail $'($url) did not answer'
    }
}

# Wait until a file exists, or until the timeout. A timeout of 0 sec makes one attempt.
def "main file" [
    path: path # The file to wait for.
    --timeout: duration = 15min # How long to try.
]: nothing -> nothing {
    let attempts = ($timeout // 1sec) + 1

    if not (poll {|| $path | path exists } {interval: 1sec, max_attempts: $attempts}) {
        fail $'($path) did not appear'
    }
}

def main []: nothing -> nothing {
    help main
}
