#!/usr/bin/env nu
# nu-lint-ignore-file: dont_mix_different_effects, string_may_be_bare

use std/log
use ./lib.nu [render-sql]

def main []: nothing -> nothing {
    let schema = $env.SCHEMA? | default pic
    let policy_dump = '/tmp/access_policy.dump'
    let log_dump = '/tmp/access_log.dump'
    let object_date = date now | format date '%Y-%m-%d'
    let s3_prefix = $'s3://($env.S3_BUCKET)/($env.BACKUP_PREFIX)/($schema)/($object_date)'
    let scheme = if ($env.S3_USE_SSL? | default 'false') == 'true' { 'https' } else { 'http' }
    let endpoint_url = if ($env.S3_ENDPOINT | str starts-with 'http') {
        $env.S3_ENDPOINT
    } else {
        $'($scheme)://($env.S3_ENDPOINT)'
    }

    log info $'Backup started schema=($schema)'

    try {
        aws configure set default.s3.addressing_style path
        aws configure set default.region auto
    } catch {|err| error make {
        msg: $'aws configure failed: ($err.msg)'
        label: {
            text: aws
            span: (metadata $schema).span
        }
    } }

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

    log info $'Uploading state dump to ($s3_prefix)/access_policy.dump...'
    try {
        aws s3 cp $policy_dump $'($s3_prefix)/access_policy.dump' --endpoint-url $endpoint_url
    } catch {|err| error make {
        msg: $'S3 upload failed for ($schema): ($err.msg)'
        label: {
            text: aws
            span: (metadata $schema).span
        }
    } }

    log info $'Uploading log dump to ($s3_prefix)/access_log.dump...'
    try {
        aws s3 cp $log_dump $'($s3_prefix)/access_log.dump' --endpoint-url $endpoint_url
    } catch {|err| error make {
        msg: $'S3 upload failed for ($schema): ($err.msg)'
        label: {
            text: aws
            span: (metadata $schema).span
        }
    } }

    log info $'Pruning access_log retention for ($schema)...'
    try {
        render-sql cleanup_access_log.sql {
            schema: $schema
            log_retention_days: $env.ACCESS_LOG_RETENTION_DAYS
        } | psql --no-psqlrc --quiet -v ON_ERROR_STOP=1
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
