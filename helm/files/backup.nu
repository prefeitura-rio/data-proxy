#!/usr/bin/env nu
# nu-lint-ignore-file: dont_mix_different_effects, string_may_be_bare

use std/log
use ./lib.nu [quote-pg]

# Return the configured object-store endpoint with an explicit scheme.
def endpoint-url []: nothing -> string {
    let endpoint = $env.S3_ENDPOINT

    if ($endpoint | str starts-with 'http') {
        return $endpoint
    }

    let scheme = if ($env.S3_USE_SSL? | default 'false') == 'true' { 'https' } else { 'http' }

    $'($scheme)://($endpoint)'
}

# Return the rclone remote environment for the configured backup target.
def rclone-env []: nothing -> record {
    let endpoint = endpoint-url

    if ($endpoint | str contains 'googleapis.com') {
        return {RCLONE_CONFIG_STORE_TYPE: 'google cloud storage', RCLONE_CONFIG_STORE_SERVICE_ACCOUNT_FILE: $env.GOOGLE_APPLICATION_CREDENTIALS, RCLONE_CONFIG_STORE_BUCKET_POLICY_ONLY: 'true'}
    }

    {
        RCLONE_CONFIG_STORE_TYPE: 's3'
        RCLONE_CONFIG_STORE_PROVIDER: 'Other'
        RCLONE_CONFIG_STORE_ENDPOINT: $endpoint
        RCLONE_CONFIG_STORE_ACCESS_KEY_ID: $env.AWS_ACCESS_KEY_ID
        RCLONE_CONFIG_STORE_SECRET_ACCESS_KEY: $env.AWS_SECRET_ACCESS_KEY
        RCLONE_CONFIG_STORE_FORCE_PATH_STYLE: 'true'
        RCLONE_CONFIG_STORE_REGION: 'auto'
        RCLONE_CONFIG_STORE_NO_CHECK_BUCKET: 'true'
    }
}

def main []: nothing -> nothing {
    let schema = $env.SCHEMA? | default pic
    let policy_dump = '/tmp/access_policy.dump'
    let log_dump = '/tmp/access_log.dump'
    let object_date = date now | format date '%Y-%m-%d'
    let object_prefix = $'($env.S3_BUCKET)/($env.BACKUP_PREFIX)/($schema)/($object_date)'
    let remote_prefix = $'store:($object_prefix)'

    log info $'Backup started schema=($schema)'

    load-env {RCLONE_CONFIG: '/dev/null'}
    load-env (rclone-env)

    log info $'Dumping ($schema).access_policy...'
    try {
        pg_dump --format=custom --no-owner --no-acl --enable-row-security --table=($'($schema).access_policy') --data-only --file=($policy_dump)
    } catch {|err| error make {
        msg: $'pg_dump failed for ($schema).access_policy: ($err.msg)'
        label: {
            text: pg_dump
            span: (metadata $schema).span
        }
    } }

    log info $'Dumping ($schema).access_log...'
    try {
        pg_dump --format=custom --no-owner --no-acl --enable-row-security --table=($'($schema).access_log') --data-only --file=($log_dump)
    } catch {|err| error make {
        msg: $'pg_dump failed for ($schema).access_log: ($err.msg)'
        label: {
            text: pg_dump
            span: (metadata $schema).span
        }
    } }

    log info $'Uploading state dump to ($object_prefix)/access_policy.dump...'
    try {
        rclone copyto $policy_dump $'($remote_prefix)/access_policy.dump'
    } catch {|err| error make {
        msg: $'S3 upload failed for ($schema): ($err.msg)'
        label: {
            text: rclone
            span: (metadata $schema).span
        }
    } }

    log info $'Uploading log dump to ($object_prefix)/access_log.dump...'
    try {
        rclone copyto $log_dump $'($remote_prefix)/access_log.dump'
    } catch {|err| error make {
        msg: $'S3 upload failed for ($schema): ($err.msg)'
        label: {
            text: rclone
            span: (metadata $schema).span
        }
    } }

    log info $'Pruning access_log retention for ($schema)...'
    let procedure_schema = quote-pg ($env.DBOS_APP_SCHEMA? | default data_proxy) identifier
    let query = [
        $"CALL ($procedure_schema).prune_access_log\("
        ":'retention'::interval, :'schema');"
    ] | str join
    try {
        psql --no-psqlrc --quiet -v ON_ERROR_STOP=1 --set $"retention=($env.ACCESS_LOG_RETENTION_DAYS) days" --set $"schema=($schema)" -c $query
    } catch {|err| error make {
        msg: $'Access log cleanup failed for ($schema): ($err.msg)'
        label: {
            text: psql
            span: (metadata $schema).span
        }
    } }

    log info $'Backup completed schema=($schema)'

    try { rm --force $policy_dump $log_dump } catch {|err| log warning $'Could not remove dumps: ($err.msg)' }
}
