use std/log
use ./lib.nu [fail]

def endpoint-url []: nothing -> string {
    if ($env.S3_ENDPOINT | str starts-with 'http') {
        return $env.S3_ENDPOINT
    }

    let scheme = if $env.S3_USE_SSL == 'true' { 'https' } else { 'http' }
    $'($scheme)://($env.S3_ENDPOINT)'
}

def verify-bucket [bucket: string]: nothing -> nothing {
    let probe = $'.data-proxy-s3-probe-(random uuid)'
    rclone lsf $'store:($bucket)'
    rclone touch $'store:($bucket)/($probe)'
    rclone cat $'store:($bucket)/($probe)' | ignore
    rclone deletefile $'store:($bucket)/($probe)'
}

def main [
    --create # Create the configured bucket when it is absent.
    --wait # Verify the configured bucket without creating it.
]: nothing -> nothing {
    if $create == $wait {
        fail 'Specify exactly one S3 bootstrap mode: --create or --wait' {
            command: main
            span: (metadata $create).span
        }
    }

    let timeout = $env.S3_BOOTSTRAP_TIMEOUT | into duration
    let retry = $env.S3_BOOTSTRAP_RETRY | into duration
    let deadline = (date now) + $timeout
    let bucket = $env.S3_BUCKET

    let endpoint = endpoint-url
    if ($endpoint | str contains 'googleapis.com') {
        load-env {
            RCLONE_CONFIG: '/dev/null'
            RCLONE_CONFIG_STORE_TYPE: 'google cloud storage'
            RCLONE_CONFIG_STORE_SERVICE_ACCOUNT_FILE: $env.GOOGLE_APPLICATION_CREDENTIALS
            RCLONE_CONFIG_STORE_BUCKET_POLICY_ONLY: 'true'
        }
    } else {
        load-env {
            RCLONE_CONFIG: '/dev/null'
            RCLONE_CONFIG_STORE_TYPE: 's3'
            RCLONE_CONFIG_STORE_PROVIDER: 'Other'
            RCLONE_CONFIG_STORE_ENDPOINT: $endpoint
            RCLONE_CONFIG_STORE_ACCESS_KEY_ID: $env.S3_ACCESS_KEY
            RCLONE_CONFIG_STORE_SECRET_ACCESS_KEY: $env.S3_SECRET_KEY
            RCLONE_CONFIG_STORE_FORCE_PATH_STYLE: 'true'
            RCLONE_CONFIG_STORE_REGION: 'auto'
        }
    }

    log info $'S3 bootstrap started: bucket=($bucket) endpoint=(endpoint-url) timeout=($timeout) retry=($retry) mode=(if $create { "create" } else { "wait" })'

    loop {
        try {
            if $create {
                rclone mkdir $'store:($bucket)'
            }

            verify-bucket $bucket
            log info $'S3 bucket is ready: bucket=($bucket)'
            return
        } catch {|err|
            if (date now) >= $deadline {
                fail $'s3-bootstrap state=failed bucket=($bucket) timeout=($timeout) error=($err.msg)' {
                    command: main
                    span: (metadata $bucket).span
                }
            }

            log warning $'S3 bucket is not ready; retrying: bucket=($bucket) retry=($retry) error=($err.msg)'
            sleep $retry
        }
    }
}
