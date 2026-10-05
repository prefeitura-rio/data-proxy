use ./lib.nu [
    apply-gcp-secret
    build-local-images
    fail
    git-root
    IMAGE_REGISTRY
    IMAGE_TAG_FILE
    local-kubeconfig
    NAMESPACE
    poll
    sync-data-proxy
    wait-for
    wrap-kubectl
]

# Read the immutable local image tag from the latest build.
def image-tag []: nothing -> string {
    let tag_file = git-root | path join $IMAGE_TAG_FILE

    try {
        open --raw $tag_file | str trim
    } catch {|err| fail $'Could not read local image tag: ($err.msg)' {
            command: k6
            span: (metadata $tag_file).span
        } }
}

# Switch between single and HA mode and wait for required workloads.
def switch-mode [kubecfg: path, ha: bool]: nothing -> nothing {
    let image_tag = image-tag
    let mode = if $ha { 'HA' } else { 'single' }

    let cluster = try {
        wrap-kubectl $kubecfg -n $NAMESPACE get cluster data-proxy -o json | from json
    } catch {
        null
    }

    let matches = (
        $cluster != null and (
            if $ha { $cluster.spec.instances >= 2 } else { $cluster.spec.instances == 1 }
        )
    )

    if not $matches {
        log info $'Switching to ($mode) mode...'
        sync-data-proxy $kubecfg $image_tag $ha
        [
            'data-proxy/data-proxy-proxy'
            'data-proxy/data-proxy-postgrest'
            'data-proxy/data-proxy-sync'
        ] | wait-for deployment $kubecfg
    } else {
        log info $'Cluster already targets ($mode) mode; skipping Helm upgrade'
    }

    log info $'Waiting for the ($mode) instances...'
    if not (poll {||
        try {
            let cluster = wrap-kubectl $kubecfg -n $NAMESPACE get cluster data-proxy -o json | from json
            let instances = $cluster.spec.instances
            let mode_matches = if $ha { $instances >= 2 } else { $instances == 1 }

            $mode_matches and ($cluster.status.readyInstances? == $instances) and ($cluster.status.phase? == 'Cluster in healthy state')
        } catch {
            false
        }
    } {
        interval: 10sec
        max_attempts: 90
    }) {
        fail $'The cluster did not settle in ($mode) mode within 15 minutes' {
            command: switch-mode
            span: (metadata $ha).span
        }
    }

    if not $ha {
        return
    }

    [
        'data-proxy/data-proxy-postgrest-ro'
        'data-proxy/data-proxy-pooler-ro'
    ] | wait-for deployment $kubecfg
}

# Read one condition type from the k6 runner Jobs.
def job-condition [kubecfg: path, label: string, type: string]: nothing -> string {
    let path = [
        'jsonpath={.items[*].status.conditions[?(@.type=="'
        $type
        '")].status}'
    ] | str join ''

    wrap-kubectl $kubecfg -n $NAMESPACE get jobs -l $label -o $path | str trim
}

# Return completion and failure flags for one k6 runner Job set.
def test-phase [kubecfg: path, label: string]: nothing -> record<complete: bool, failed: bool> {
    {
        complete: ((job-condition $kubecfg $label Complete) == 'True')
        failed: ((job-condition $kubecfg $label Failed) == 'True')
    }
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
        fail $'($description) failed: ($err.msg)' {
            command: k6
            span: (metadata $job).span
        }
    }
}

# Create a k6 ConfigMap, run one TestRun, and print the runner output.
def k6-run [
    kubecfg: path
    configmap: string
    script_key: string
    script_path: string
    testrun: string
    yaml_path: path
    --profile: string = ''
    --image-tag: string = 'local'
    --set: record = {}
    --with-trigger # Include trigger.py for an E2E TestRun.
]: nothing -> nothing {
    let files = [
        $'--from-file=($script_key)=($script_path)'
        '--from-file=lib.ts=k6/lib.ts'
    ] | if $with_trigger {
        append '--from-file=trigger.py=scripts/manifests/files/trigger.py'
    } else { }

    log info $'Creating configmap ($configmap)...'
    wrap-kubectl $kubecfg -n $NAMESPACE create configmap $configmap ...$files --dry-run=client -o yaml
    | wrap-kubectl $kubecfg apply -f -

    log info $'Deleting previous testrun ($testrun)...'
    wrap-kubectl $kubecfg -n $NAMESPACE delete testrun $testrun --ignore-not-found

    let raw = try {
        open --raw $yaml_path
    } catch {|err| fail $'Failed to open ($yaml_path): ($err.msg)' {
            command: k6-run
            span: (metadata $yaml_path).span
        } }

    let named = $raw
    | str replace --regex '(?m)(kind: TestRun\nmetadata:\n\s+name: )[^\n]+' (['${1}' $testrun] | str join '')

    let profiled = if ($profile | is-empty) {
        $named
    } else {
        $named | str replace --all 'value: load' $'value: ($profile)'
    }

    let images = [
        {
            source: $'($IMAGE_REGISTRY)/k6:local'
            target: $'($IMAGE_REGISTRY)/k6:($image_tag)'
        }
        {
            source: $'($IMAGE_REGISTRY)/data-proxy-postgres:17.0.0-local'
            target: $'($IMAGE_REGISTRY)/data-proxy-postgres:17.0.0-($image_tag)'
        }
    ] | reduce --fold $profiled {|replacement, text|
        $text | str replace --all $replacement.source $replacement.target
    }

    let yaml = $set
    | items {|name, value|
        {
            name: $name
            value: $value
        }
    }
    | reduce --fold $images {|entry, text|
        $text | str replace --regex (['(?m)(- name: ' $entry.name '\n\s+value: ).*'] | str join '') (['${1}"' $entry.value '"'] | str join '')
    }

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
        fail $"k6 runner Job didn't appear within 15 minutes (TestRun stage=($stage))" {
            command: k6-run
            span: (metadata $testrun).span
        }
    }

    log info 'Waiting for the test to complete...'
    let deadline = (date now) + 60min
    mut phase = test-phase $kubecfg $label
    while (date now) < $deadline and not $phase.complete and not $phase.failed {
        sleep 2sec
        $phase = test-phase $kubecfg $label
    }

    let pod = wrap-kubectl $kubecfg -n $NAMESPACE get pods -l $label -o 'jsonpath={.items[0].metadata.name}'
    let runner_log = wrap-kubectl $kubecfg -n $NAMESPACE logs $pod
    print ($runner_log | to text)

    if $phase.failed {
        fail 'k6 runner Job failed' {
            command: k6-run
            span: (metadata $testrun).span
        }
    }

    if not $phase.complete {
        fail 'k6 runner Job timed out after 60 minutes' {
            command: k6-run
            span: (metadata $testrun).span
        }
    }
}

# Run one k6 performance profile in the requested mode.
def run-perf [kubecfg: path, profile: string, ha: bool]: nothing -> nothing {
    switch-mode $kubecfg $ha
    run-cronjob $kubecfg manifests-sync-trigger 'normal sync Job'

    let image_tag = image-tag
    k6-run $kubecfg data-proxy-k6 perf.ts k6/perf.ts data-proxy-perf k6/perf.yaml --profile $profile --image-tag $image_tag --set {
        HA_MODE: (if $ha { 'true' } else { 'false' })
    }
}

# Run the k6 smoke profile: one virtual user for 40 seconds.
export def "main k6 smoke" [
    --ha # Run in HA mode.
]: nothing -> nothing {
    run-perf (local-kubeconfig) smoke $ha
}

# Run the standard k6 load profile.
export def "main k6 load" [
    --ha # Run in HA mode.
]: nothing -> nothing {
    run-perf (local-kubeconfig) load $ha
}

# Run the k6 stress profile.
export def "main k6 stress" [
    --ha # Run in HA mode.
]: nothing -> nothing {
    run-perf (local-kubeconfig) stress $ha
}

# Run the full E2E suite against the requested deployment mode.
export def "main k6 e2e" [
    --ha # Deploy and verify HA mode.
]: nothing -> nothing {
    let kubecfg = local-kubeconfig
    let mode = if $ha { 'ha' } else { 'single' }
    let repo = git-root

    try {
        cd $repo
    } catch {|err| fail $'Could not change to repository root: ($err.msg)' {
            command: k6-e2e
            span: (metadata $repo).span
        } }

    log info 'Waiting for the existing Data Proxy release...'
    [
        'data-proxy/data-proxy-proxy'
        'data-proxy/data-proxy-postgrest'
        'data-proxy/data-proxy-sync'
    ] | wait-for deployment $kubecfg

    log info 'Clearing test resources...'
    run-cronjob $kubecfg manifests-cleanup 'test cleanup Job'

    let image_tag = build-local-images

    apply-gcp-secret $kubecfg

    log info 'Deleting init-db Jobs so they recreate PostgreSQL setup...'
    let old_init_jobs = wrap-kubectl $kubecfg -n $NAMESPACE get jobs -l app.kubernetes.io/component=init-db -o name
    | lines
    if ($old_init_jobs | is-not-empty) {
        wrap-kubectl $kubecfg -n $NAMESPACE delete ...$old_init_jobs --ignore-not-found
    }

    log info 'Syncing data-proxy release with Helmfile...'
    sync-data-proxy $kubecfg $image_tag $ha

    log info 'Waiting for the init-db Job...'
    let init_jobs = wrap-kubectl $kubecfg -n $NAMESPACE get jobs -l app.kubernetes.io/component=init-db -o name
    | lines

    for job in $init_jobs {
        wrap-kubectl $kubecfg -n $NAMESPACE wait --for=condition=complete $job --timeout=360s
    }

    log info 'Waiting for data-proxy deployments...'
    [
        'data-proxy/data-proxy-proxy'
        'data-proxy/data-proxy-postgrest'
        'data-proxy/data-proxy-sync'
    ] | wait-for deployment $kubecfg

    log info $'Running the full E2E suite against ($mode) mode...'
    k6-run $kubecfg data-proxy-e2e e2e.ts k6/e2e.ts $'data-proxy-e2e-($mode)' k6/e2e.yaml --image-tag $image_tag --set {MODE: $mode} --with-trigger
}
