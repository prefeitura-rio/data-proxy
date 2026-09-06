#!/usr/bin/env nu

let writers_file = $env.SCHEMA_WRITERS_FILE? | default "/config/schema-writers/writers.json"
let writer_dsn = open $writers_file | get $env.SCHEMA

if ($writer_dsn | is-empty) {
    print --stderr $"No writer DSN found for schema ($env.SCHEMA)"
    exit 1
}

let dsn_parts = $writer_dsn | parse "postgresql://{user_pass}@{host_db}"
let host_db = $dsn_parts.host_db
let backup_dsn = $"postgresql://backup:($env.BACKUP_PASSWORD)@($host_db)"

let scheme = if $env.GCS_USE_SSL == "true" { "https" } else { "http" }
let object_date = date now | format date "%Y-%m-%d"
let gcs_object = $"s3://($env.GCS_BUCKET)/($env.BACKUP_PREFIX)/($env.SCHEMA)/($object_date).csv.age"
let endpoint_url = $"($scheme)://($env.GCS_ENDPOINT)"


^aws configure set default.s3.addressing_style path
^aws configure set default.region auto

let sql = $"COPY ($env.SCHEMA).access_policy TO STDOUT WITH (FORMAT csv, HEADER true)"

^psql $backup_dsn --no-psqlrc --quiet --command $sql
| ^age --encrypt --recipient $env.AGE_RECIPIENT
| ^aws s3 cp - $gcs_object --endpoint-url $endpoint_url
