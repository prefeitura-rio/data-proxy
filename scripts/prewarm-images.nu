use lib.nu [fail poll ]

# Print image preparation logs and stop with a failure.
def fail-image [folder: path, kind: string, message: string]: nothing -> error {
    let log = $folder | path join $'($kind).log'

    if ($log | path exists) {
        print ((open --raw $log | decode utf-8))
    }

    fail $message
}

# Wait for one image preparation result and verify the expected image.
def verify-image [
    folder: path
    kind: string
    image: string
    timeout: duration
]: nothing -> nothing {
    let status = $folder | path join $'($kind).status'

    if not ($status | path exists) {
        let pid_file = $folder | path join $'($kind).pid'

        if not ($pid_file | path exists) {
            (fail-image
                $folder
                $kind
                $'Missing ($kind) image preparation process.'
            )
        }

        let pid = (open --raw $pid_file | decode utf-8) | str trim | into int
        let attempts = [
            1
            (($timeout | into int) / (100ms | into int) | math ceil | into int)
        ] | math max
        let completed = poll {||
            if ($status | path exists) {
                return true
            }

            let live = ps | where pid == $pid and status != Zombie | is-not-empty

            if not $live and not ($status | path exists) {
                fail-image $folder $kind $'The ($kind) image process exited without a completion record.'
            }

            $status | path exists
        } {interval: 100ms, max_attempts: $attempts}

        if not $completed {
            (fail-image
                $folder
                $kind
                $'Timed out waiting for the ($kind) image preparation process.'
            )
        }
    }

    if ((open --raw $status | decode utf-8) | str trim) != '0' {
        (fail-image
            $folder
            $kind
            $'The ($kind) test image preparation failed.'
        )
    }

    if (podman image exists $image | complete).exit_code != 0 {
        (fail-image
            $folder
            $kind
            $'The expected test image is missing: ($image)'
        )
    }
}

# Verify background test image preparation before integration tests.
def main []: nothing -> nothing {
    let postgres = $env.POSTGRES_IMAGE?

    if $postgres in [null ''] {
        fail 'The prewarm action did not return a PostgreSQL image.'
    }

    let folder = $env.RUNNER_TEMP | path join data-proxy-prewarm
    let timeout = $'($env.PREWARM_TIMEOUT? | default "900")sec' | into duration

    if $timeout <= 0sec {
        fail 'The image preparation timeout must be positive.'
    }

    verify-image $folder postgres $postgres $timeout

    let keycloak = $env.KEYCLOAK_IMAGE?

    if $keycloak not-in [null ''] {
        verify-image $folder keycloak $keycloak $timeout
    }
}
