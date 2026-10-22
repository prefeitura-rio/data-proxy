use std/log
use lib.nu [
    apply-gcp-secret
    build-k6-image
    build-local-images
    fail
    git-root
    IMAGE_REGISTRY
    local-image-tag
    local-kubeconfig
    NAMESPACE
    poll
    read-data
    wrap-helmfile
    wait-for
    wrap-kubectl
]

# Wait until the cluster has settled on its standbys and the serving workloads are available.
def wait-for-topology [kubecfg: path]: nothing -> nothing {
    log info 'Waiting for the PostgreSQL instances...'
    if not (poll {||
        try {
            let cluster = wrap-kubectl $kubecfg -n $NAMESPACE get cluster data-proxy -o json | from json
            let instances = $cluster.spec.instances

            ($instances >= 2) and ($cluster.status.readyInstances? == $instances) and ($cluster.status.phase? == 'Cluster in healthy state')
        } catch {
            false
        }
    } {
        interval: 10sec
        max_attempts: 90
    }) {
        fail 'The cluster did not settle on a primary and a standby within 15 minutes'
    }

    [
        'data-proxy/data-proxy-pgdog'
        'data-proxy/data-proxy-postgrest'
    ] | wait-for deployment $kubecfg
}

# Return completion and failure flags for one k6 runner Job set.
def test-phase [kubecfg: path, label: string]: nothing -> record<complete: bool, failed: bool> {
    let conditions = try {
        wrap-kubectl $kubecfg -n $NAMESPACE get jobs -l $label -o json
        | from json
        | get items
        | each {|job| $job.status.conditions? | default [] }
        | flatten
    } catch {|err| fail $'Could not read the k6 runner Jobs: ($err.msg)' }

    {
        complete: (
            $conditions
            | any {|condition| $condition.type == 'Complete' and $condition.status == 'True' }
        )
        failed: (
            $conditions
            | any {|condition| $condition.type == 'Failed' and $condition.status == 'True' }
        )
    }
}

# Wait until the runner Jobs complete or fail, and return their last phase.
def await-test [kubecfg: path, label: string, attempts: int]: nothing -> record<complete: bool, failed: bool> {
    poll {|| let phase = test-phase $kubecfg $label; $phase.complete or $phase.failed } {interval: 2sec, max_attempts: $attempts} | ignore

    test-phase $kubecfg $label
}

# Run one Job from a Helm-managed suspended CronJob and wait for completion.
def run-cronjob [kubecfg: path, cronjob: string, description: string]: nothing -> nothing {
    let job = $'($cronjob)-(date now | format date '%Y%m%d%H%M%S')'

    log info $'Starting ($description) ($job)...'
    wrap-kubectl $kubecfg -n $NAMESPACE create job $job $'--from=cronjob/($cronjob)'

    try {
        wrap-kubectl $kubecfg -n $NAMESPACE wait --for=condition=complete $'job/($job)' --timeout=10m
    } catch {|err|
        let logs = try {
            wrap-kubectl $kubecfg -n $NAMESPACE logs $'job/($job)'
        } catch {
            ''
        }
        log error $logs
        fail $'($description) failed: ($err.msg)'
    }
}

# Print the log of one pod as it grows, once the container has started. Returns false when the stream failed.
def stream-log [kubecfg: path, pod: string]: nothing -> bool {
    let started = poll {||
        try {
            wrap-kubectl $kubecfg -n $NAMESPACE logs $pod --tail=1 | ignore
            true
        } catch {
            false
        }
    } {interval: 2sec, max_attempts: 150}

    if not $started { return false }

    try {
        for line in (wrap-kubectl $kubecfg -n $NAMESPACE logs --follow $pod | lines) {
            print $line
        }

        true
    } catch {
        false
    }
}

# Return the TestRun manifest from its file, with a new name, the image of this run, and the settings in its environment.
def testrun-manifest [yaml_path: path, testrun: string, image_tag: string, settings: record]: nothing -> string {
    let env_names = read-data $yaml_path | get spec.runner.env | get name
    let added = $settings
    | columns
    | where $it not-in $env_names
    | each {|name| {name: $name, value: ($settings | get --optional $name | into string)} }

    read-data $yaml_path
    | update metadata.name $testrun
    | update spec.runner.image $'($IMAGE_REGISTRY)/k6:($image_tag)'
    | update spec.runner.env {|run|
        $run.spec.runner.env
        | each {|entry|
            match ($settings | get --optional $entry.name) {
                null => $entry
                $value => {name: $entry.name, value: ($value | into string)}
            }
        }
        | append $added
    }
    | to yaml
}

# Build the k6 image with the scripts in it, run one TestRun from it, and print the runner output.
def k6-run [
    kubecfg: path
    testrun: string
    yaml_path: path
    --set: record = {}
    --with-trigger # Put trigger.py and mutate.py in the ConfigMap that the E2E scenarios mount into their Jobs.
]: nothing -> nothing {
    if $with_trigger {
        log info 'Creating configmap data-proxy-e2e...'
        (wrap-kubectl $kubecfg -n $NAMESPACE create configmap data-proxy-e2e
            --from-file=trigger.py=k3d/manifests/files/trigger.py
            --from-file=mutate.py=k3d/manifests/files/mutate.py
            --dry-run=client -o yaml)
        | wrap-kubectl $kubecfg apply -f -
    }

    let image_tag = build-k6-image

    log info $'Deleting previous testrun ($testrun)...'
    wrap-kubectl $kubecfg -n $NAMESPACE delete testrun $testrun --ignore-not-found

    let yaml = testrun-manifest $yaml_path $testrun $image_tag $set

    log info $'Applying testrun ($testrun)...'
    $yaml | wrap-kubectl $kubecfg apply -f -

    let label = $'k6_cr=($testrun),runner=true'
    log info 'Waiting for the runner job to appear...'
    if not (poll {||
        let jobs = wrap-kubectl $kubecfg -n $NAMESPACE get jobs -l $label -o 'jsonpath={.items}' | str trim
        ($jobs | is-not-empty) and ($jobs != '[]')
    } {
        interval: 1sec
        max_attempts: 900
    }) {
        let stage = try {
            wrap-kubectl $kubecfg -n $NAMESPACE get testrun $testrun -o json
            | from json
            | get status.stage?
            | default unknown
        } catch {
            missing
        }
        fail $"k6 runner Job didn't appear within 15 minutes (TestRun stage=($stage))"
    }

    let pod = wrap-kubectl $kubecfg -n $NAMESPACE get pods -l $label -o 'jsonpath={.items[0].metadata.name}'
    log info $'Streaming the log of the runner ($pod)...'
    let streamed = stream-log $kubecfg $pod

    log info 'Waiting for the test to complete...'
    let phase = await-test $kubecfg $label 1800

    if not $streamed {
        print (wrap-kubectl $kubecfg -n $NAMESPACE logs $pod | to text)
    }

    if $phase.failed {
        fail 'k6 runner Job failed'
    }

    if not $phase.complete {
        fail 'k6 runner Job timed out after 60 minutes'
    }
}

# The k6 setting of each traffic share.
const SHARE_SETTINGS = {
    bottleneck: K6_BOTTLENECK_SHARE
    fallback: K6_FALLBACK_SHARE
}

# Parse one traffic share in the form bottleneck=0.01 into the name of its setting and its value.
def parse-share [share: string]: nothing -> record<setting: string, value: float> {
    let parts = $share | split row '='

    if ($parts | length) != 2 {
        fail $'Invalid share: ($share). Use bottleneck=0.01 or fallback=0.01.'
    }

    let setting = $SHARE_SETTINGS | get --optional ($parts | first)

    if $setting == null {
        fail $'Unknown share: ($parts | first). Use bottleneck or fallback.'
    }

    let value = try { $parts | last | into float } catch { fail $'Invalid share value: ($share)' }

    {setting: $setting, value: $value}
}

# Parse traffic shares in the form bottleneck=0.01 fallback=0.01.
def parse-shares [...shares: string]: nothing -> record {
    let parsed = $shares | each {|share| parse-share $share }

    $parsed | reduce --fold {} {|share, result| $result | upsert $share.setting $share.value }
}

# Run one k6 profile.
def run-profile [
    kubecfg: path
    profile: string
    settings: record = {}
    --cache-ttl: int = 0
]: nothing -> nothing {
    wait-for-topology $kubecfg

    if $cache_ttl > 0 {
        let image_tag = local-image-tag
        log info $'Applying proxy cache TTL ($cache_ttl)s...'
        wrap-helmfile $kubecfg --image-tag $image_tag sync --selector name=data-proxy --state-values-set $'proxy.cacheTtl=($cache_ttl)'
        [
            'data-proxy/data-proxy-proxy'
            'data-proxy/data-proxy-postgrest'
            'data-proxy/data-proxy-sync'
        ] | wait-for deployment $kubecfg
    }
    run-cronjob $kubecfg manifests-cleanup 'test cleanup Job'
    run-cronjob $kubecfg manifests-sync-trigger 'normal sync Job'

    let run_settings = {
        K6_PROFILE: $profile
    } | merge $settings

    k6-run $kubecfg data-proxy-load k6/load.yaml --set $run_settings
}

# Run the k6 smoke profile: one virtual user for 40 seconds.
export def "main k6 smoke" []: nothing -> nothing {
    run-profile (local-kubeconfig) smoke
}

# Run the standard k6 load profile.
export def "main k6 load" [
    --capacity # Use capacity thresholds and extra VU headroom.
    --report-only # Do not gate fallback and heavy-route thresholds.
    --peak-rate: int = 25 # Peak iterations per second.
    --cache-ttl: int = 300 # Proxy cache TTL used for this run.
    --shares: string = '' # First traffic share, for example: --shares bottleneck=0.01 fallback=0.01.
    ...share_values: string
]: nothing -> nothing {
    let share_args = [$shares ...$share_values]
    | where $it != ''
    let share_settings = parse-shares ...$share_args
    let threshold_mode = if $capacity { 'capacity' } else { 'strict' }
    let max_vus_multiplier = if $capacity { 6 } else { 3 }
    let settings = $share_settings
    | merge {
            K6_PEAK_RATE: $peak_rate
            K6_THRESHOLD_MODE: $threshold_mode
            K6_MAX_VUS_MULTIPLIER: $max_vus_multiplier
            K6_GATE_CLIENT_RISK: (if $report_only { 'false' } else { 'true' })
        }
    run-profile (local-kubeconfig) load $settings --cache-ttl $cache_ttl
}

# Run the full E2E suite against the deployed release.
export def "main k6 e2e" [
    --build # Build and deploy fresh local images before the E2E run.
    --scenarios: string = '' # Comma-separated scenarios to run, such as `cache,access`. Empty runs all.
]: nothing -> nothing {
    let kubecfg = local-kubeconfig
    let repo = git-root

    try {
        cd $repo
    } catch {|err| fail $'Could not change to repository root: ($err.msg)' }

    log info 'Waiting for the existing Data Proxy release...'
    [
        'data-proxy/data-proxy-proxy'
        'data-proxy/data-proxy-postgrest'
        'data-proxy/data-proxy-sync'
    ] | wait-for deployment $kubecfg

    log info 'Clearing test resources...'
    run-cronjob $kubecfg manifests-cleanup 'test cleanup Job'

    let image_tag = if $build {
        apply-gcp-secret $kubecfg --required
        let tag = build-local-images

        log info 'Deleting configure-db Jobs so they recreate PostgreSQL setup...'
        let old_configure_jobs = wrap-kubectl $kubecfg -n $NAMESPACE get jobs -l app.kubernetes.io/component=configure-db -o name
        | lines
        if ($old_configure_jobs | is-not-empty) {
            wrap-kubectl $kubecfg -n $NAMESPACE delete ...$old_configure_jobs --ignore-not-found
        }

        log info 'Syncing local Helmfile releases with fresh images...'
        wrap-helmfile $kubecfg --image-tag $tag sync
        $tag
    } else {
        wait-for-topology $kubecfg
        local-image-tag
    }

    let selected = if ($scenarios | is-empty) { 'all' } else { $scenarios }
    log info $'Running the E2E suite: build=($build), scenarios=($selected)'
    k6-run $kubecfg data-proxy-e2e k6/e2e.yaml --set {
        PG_IMAGE: $'($IMAGE_REGISTRY)/data-proxy-postgres:17.0.0-($image_tag)'
        E2E_SCENARIOS: $scenarios
    } --with-trigger
}
