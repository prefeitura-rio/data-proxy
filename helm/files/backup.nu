#!/usr/bin/env nu

# nu-lint-ignore-file: dont_mix_different_effects, string_may_be_bare

use std/log

def main []: nothing -> nothing {
    let schema = $env.SCHEMA? | default pic
    let dump_file = $'/tmp/($schema)_access_policy.dump'
    let object_date = date now | format date '%Y-%m-%d'
    let s3_object = $'s3://($env.S3_BUCKET)/($env.BACKUP_PREFIX)/($schema)/($object_date).dump'
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

    log info $'Dumping ($schema).access_policy…'
    try {
        pg_dump --format=custom --no-owner --no-acl --table=($'($schema).access_policy') --data-only --file=$dump_file
    } catch {|err| error make {
        msg: $'pg_dump failed for ($schema).access_policy: ($err.msg)'
        label: {
            text: pg_dump
            span: (metadata $schema).span
        }
    } }

    log info $'Uploading to ($s3_object)…'
    try {
        aws s3 cp $dump_file $s3_object --endpoint-url $endpoint_url
        log info $'Backup completed schema=($schema) object=($s3_object)'
    } catch {|err| error make {
        msg: $'S3 upload failed for ($schema): ($err.msg)'
        label: {
            text: aws
            span: (metadata $schema).span
        }
    } }

    try { rm --force $dump_file } catch {|_|

    }
}
