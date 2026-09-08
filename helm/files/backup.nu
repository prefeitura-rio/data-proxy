#!/usr/bin/env nu
# nu-lint-ignore-file: dont_mix_different_effects, string_may_be_bare

use std/log

let writers_file = $env.SCHEMA_WRITERS_FILE? | default ('/' | path join config schema-writers writers.json)

let writer_dsn = try {
    open $writers_file | get writers | get --optional $env.SCHEMA
} catch {|err|
    log error $'No writer DSN found for schema ($env.SCHEMA): ($err.msg)'
    exit 1
}

let dsn_parts = $writer_dsn | parse 'postgresql://{user}:{pass}@{host}:{port}/{db}' | first
let pghost = $dsn_parts.host
let pgport = $dsn_parts.port
let pgdb = $dsn_parts.db

$env.PGPASSWORD = $env.BACKUP_PASSWORD

let scheme = if $env.GCS_USE_SSL == 'true' { 'https' } else { 'http' }
let object_date = date now | format date '%Y-%m-%d'
let gcs_object = $'s3://($env.GCS_BUCKET)/($env.BACKUP_PREFIX)/($env.SCHEMA)/($object_date).csv.age'
let endpoint_url = $'($scheme)://($env.GCS_ENDPOINT)'

log info $'Backup started schema=($env.SCHEMA)'

try {
    aws configure set default.s3.addressing_style path
    aws configure set default.region auto
} catch {|err|
    log error $'aws configure failed: ($err.msg)'
    exit 1
}

let sql = $'COPY ($env.SCHEMA).access_policy TO STDOUT WITH ' + '(FORMAT csv, HEADER true)'

let csv = try {
    psql -h $pghost -p $pgport -U backup -d $pgdb --no-psqlrc --quiet --command $sql
} catch {|err|
    log error $'psql failed: ($err.msg)'
    exit 1
}

if ($csv | is-empty) {
    log error 'psql produced no output'
    exit 1
}

try {
    $csv
    | age --encrypt --recipient $env.AGE_RECIPIENT
    | aws s3 cp - $gcs_object --endpoint-url $endpoint_url
    log info $'Backup completed schema=($env.SCHEMA) object=($gcs_object)'
} catch {|err|
    log error $'Backup failed schema=($env.SCHEMA): ($err.msg)'
    exit 1
}
