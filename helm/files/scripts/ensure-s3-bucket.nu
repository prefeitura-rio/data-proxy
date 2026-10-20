use std/log
use ./lib.nu [endpoint-url fail rclone-env wait-until]

# Read, write and delete one object, to prove that the credentials work on the bucket.
def verify-bucket [bucket: string]: nothing -> nothing {
    let probe = $'.data-proxy-s3-probe-(random uuid)'
    rclone lsf $'store:($bucket)'
    rclone touch $'store:($bucket)/($probe)'
    rclone cat $'store:($bucket)/($probe)' | ignore
    rclone deletefile $'store:($bucket)/($probe)'
}

# Allow the notebook origins to read the files with a range request, and expose the headers that DuckDB reads.
def put-cors [bucket: string, ...origins: string]: nothing -> nothing {
    let allowed = $origins | each {|origin| $'<AllowedOrigin>($origin)</AllowedOrigin>' } | str join
    let exposed = [Content-Length Content-Range ETag Accept-Ranges] | each {|name| $'<ExposeHeader>($name)</ExposeHeader>' } | str join

    $'<CORSConfiguration><CORSRule>($allowed)<AllowedMethod>GET</AllowedMethod><AllowedMethod>HEAD</AllowedMethod><AllowedHeader>Range</AllowedHeader>($exposed)<MaxAgeSeconds>3000</MaxAgeSeconds></CORSRule></CORSConfiguration>'
    | curl --silent --show-error --fail --request PUT $'(endpoint-url)/($bucket)?cors' --aws-sigv4 'aws:amz:us-east-1:s3' --user $'($env.S3_ACCESS_KEY):($env.S3_SECRET_KEY)' --header 'Content-Type: application/xml' --data-binary @-
    | ignore
}

# Check that the object-store bucket exists, and create it or wait for it.
def main [
    --create # Create the configured bucket when it is absent.
    --wait # Verify the configured bucket without creating it.
]: nothing -> nothing {
    if $create == $wait {
        fail 'Specify exactly one S3 bootstrap mode: --create or --wait'
    }

    let bucket = $env.S3_BUCKET
    let gcs = endpoint-url | str contains 'googleapis.com'
    let remote = rclone-env --access-key $env.S3_ACCESS_KEY --secret-key $env.S3_SECRET_KEY
    let remote = if $gcs { $remote } else { $remote | upsert RCLONE_CONFIG_STORE_NO_CHECK_BUCKET 'false' }
    let origins = $env.S3_CORS_ORIGINS? | default '' | split row ',' | where $it != ''

    log info $'S3 bootstrap started: bucket=($bucket) endpoint=(endpoint-url) mode=(if $create { "create" } else { "wait" })'

    with-env ({RCLONE_CONFIG: '/dev/null'} | merge $remote) {
        wait-until $'S3 bucket ($bucket)' --timeout ($env.S3_BOOTSTRAP_TIMEOUT | into duration) --interval ($env.S3_BOOTSTRAP_RETRY | into duration) {||
            try {
                if $create {
                    rclone mkdir $'store:($bucket)'
                }

                verify-bucket $bucket

                if ($origins | is-not-empty) and not $gcs {
                    put-cors $bucket ...$origins
                    log info $'S3 CORS rule is set: bucket=($bucket) origins=($origins | str join ",")'
                }

                true
            } catch {|err|
                log warning $'S3 bucket is not ready: bucket=($bucket) error=($err.msg)'
                false
            }
        }
    }
}
