#!/usr/bin/env nu

# nu-lint-ignore-file: dont_mix_different_effects, unhandled_external_error

use std/log

# Wrapped kubectl with optional context selection.
def --wrapped k [...rest: string]: nothing -> string {
    let ctx = $env.KUBE_CONTEXT?
    if $ctx == null or ($ctx | is-empty) {
        try { kubectl ...$rest } catch {|err| error make {
            msg: $'kubectl failed: ($err.msg)'
            label: {
                text: k
                span: (metadata $rest).span
            }
        } }
    } else {
        try { kubectl --context=($ctx) ...$rest } catch {|err| error make {
            msg: $'kubectl failed: ($err.msg)'
            label: {
                text: k
                span: (metadata $rest).span
            }
        } }
    }
}

# Return the Kubernetes namespace from the environment, defaulting to data-proxy.
def namespace []: nothing -> string {
    $env.NAMESPACE? | default data-proxy
}

# Return the producer CronJob name from the environment, defaulting to data-proxy-producer.
def producer-name []: nothing -> string {
    $env.PRODUCER? | default data-proxy-producer
}

# Suspend the producer CronJob so no new syncs start.
def block-syncs []: nothing -> string {
    let ns = namespace
    let producer = producer-name
    log info $'Suspending producer CronJob ($producer)…'
    k -n $ns patch cronjob $producer -p '{"spec":{"suspend":true}}' --type=merge
}

# Resume the producer CronJob so syncs can start again.
def unblock-syncs []: nothing -> string {
    let ns = namespace
    let producer = producer-name
    log info $'Unsuspending producer CronJob ($producer)…'
    (k
        -n
        $ns
        patch
        cronjob
        $producer
        -p
        '{"spec":{"suspend":false}}'
        --type=merge
    )
}

# Wait for init-db to complete by checking access_policy table exists in target.
def wait-for-schema [schema: string, dsn: string]: any -> error {
    log info $'Waiting for ($schema).access_policy in target cluster…'
    let query = $"SELECT EXISTS \(SELECT FROM pg_tables WHERE schemaname = '($schema)' AND tablename = 'access_policy'\) AND EXISTS \(SELECT FROM pg_extension WHERE extname = 'pg_duckdb'\) AND EXISTS \(SELECT FROM pg_extension WHERE extname = 'pg_partman'\)"
    for _ in 1..60 {
        let exists = try {
            psql $dsn --tuples-only --no-psqlrc --quiet -c $query | str trim
        } catch {|_| 'f' }
        if $exists == t {
            log info $'Schema ($schema) ready.'
            return
        }
        sleep 5sec
    }
    error make {
        msg: $'Schema ($schema) not ready after 300s — init-db may have failed'
        label: {
            text: wait-for-schema
            span: (metadata $schema).span
        }
    }
}

# Dump one schema from source, restore into target, reload PostgREST. Idempotent.
def migrate-schema [m: record]: nothing -> nothing {
    let dump_file = $'/tmp/($m.schema).dump'

    log info $'Dumping schema ($m.schema) from ($m.source)…'
    try {
        pg_dump $m.source --format=custom --no-owner --no-acl --schema=($m.schema) --file $dump_file
    } catch {|err| error make {
        msg: $'pg_dump failed for schema ($m.schema): ($err.msg)'
        label: {
            text: pg_dump
            span: (metadata $m).span
        }
    } }

    log info $'Restoring schema ($m.schema) into ($m.target)…'
    try {
        pg_restore --clean --if-exists --no-owner --no-acl --dbname=($m.target) $dump_file
    } catch {|err| error make {
        msg: $'pg_restore failed for schema ($m.schema): ($err.msg)'
        label: {
            text: pg_restore
            span: (metadata $m).span
        }
    } }

    log info $'Reloading PostgREST schema cache for ($m.schema)…'
    try {
        "NOTIFY pgrst, 'reload schema'" | psql $m.target --no-psqlrc --quiet
    } catch {|err| error make {
        msg: $'psql NOTIFY failed for schema ($m.schema): ($err.msg)'
        label: {
            text: psql
            span: (metadata $m).span
        }
    } }

    try { rm --force $dump_file } catch {|_|

    }
    log info $'Schema ($m.schema) migrated.'
}

# Record migration state in a ConfigMap.
def save-mode-state [state: record]: nothing -> nothing {
    let mode = $state.mode
    let status = $state.status
    let direction = $state.direction
    let ns = namespace
    let rel = $env.RELEASE_NAME? | default data-proxy
    let cm = $'($rel)-mode-state'
    let updated = date now | format date %Y-%m-%dT%H:%M:%S%z
    let yaml = (k -n $ns create configmap $cm
        $'--from-literal=mode=($mode)'
        $'--from-literal=status=($status)'
        $'--from-literal=direction=($direction)'
        $'--from-literal=updated_at=($updated)'
        --dry-run=client -o yaml)
    $yaml | kubectl -n $ns apply -f -
}

# Run the migration in the given direction.
def run-migration [direction: string]: nothing -> nothing {
    let schemas = $env.SCHEMAS | split row ' '

    if ($schemas | is-empty) {
        error make {
            msg: 'SCHEMAS environment variable must list at least one schema'
            label: {
                text: main
                span: (metadata $env.SCHEMAS).span
            }
        }
    }

    save-mode-state {mode: $env.SOURCE_MODE, status: running, direction: $direction}
    block-syncs

    try {
        for schema in $schemas {
            let m = match $direction {
                'to-ha' => {
                    {
                        schema: $schema
                        source: $env.SOURCE_DSN
                        target: ($env.TARGET_DSN | str replace '{schema}' $schema)
                    }
                }
                _ => {
                    {
                        schema: $schema
                        source: ($env.TARGET_DSN | str replace '{schema}' $schema)
                        target: $env.SOURCE_DSN
                    }
                }
            }
            wait-for-schema $m.schema $m.target
            migrate-schema $m
        }
    } catch {|err|
        save-mode-state {mode: $env.SOURCE_MODE, status: failed, direction: $direction}
        unblock-syncs
        error make {
            msg: $'Migration failed: ($err.msg)'
            label: {
                text: main
                span: (metadata $err).span
            }
        }
    }

    unblock-syncs
    save-mode-state {mode: $env.TARGET_MODE, status: completed, direction: $direction}
    log info 'All schemas migrated. Producer resumed.'
}

def main []: nothing -> nothing {
    let direction = match [$env.SOURCE_MODE? $env.TARGET_MODE] {
        ['shared' 'per-schema'] => 'to-ha'
        ['per-schema' 'shared'] => 'to-single'
        _ => 'none'
    }

    match $direction {
        'none' => {
            save-mode-state {mode: $env.TARGET_MODE, status: completed, direction: none}
            log info 'No migration needed — target mode recorded.'
        }
        _ => {
            log info $'Starting migration ($direction)…'
            run-migration $direction
        }
    }
}
