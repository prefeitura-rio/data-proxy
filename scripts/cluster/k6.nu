
use ../lib.nu [fail poll]
use ./cleanup.nu [clear-test-resources]
use ./lib.nu [git-root wrap-kubectl namespace]
use ./images.nu [refresh-proxy]
use ./mode.nu [switch-mode]

# Return whether a k6 runner Job has been created.
export def is-runner-job-ready [kubecfg: path, label: string]: nothing -> bool {
    let jobs = wrap-kubectl $kubecfg -n (namespace) get jobs -l $label -o 'jsonpath={.items}' | str trim
    ($jobs | is-not-empty) and ($jobs != '[]')
}

# Return the k6 operator stage for one TestRun, if it exists.
export def testrun-stage [kubecfg: path, testrun: string]: nothing -> string {
    try {
        wrap-kubectl $kubecfg -n (namespace) get testrun $testrun -o json
        | from json
        | get status.stage?
        | default unknown
    } catch {
        missing
    }
}

# Convert runner Job conditions into completion and failure flags.
export def test-phase-from-status [complete: string, failed: string]: nothing -> record<complete: bool, failed: bool> {
    {
        complete: ($complete == 'True')
        failed: ($failed == 'True')
    }
}

# Render a TestRun manifest with the requested name, profile, and environment values.
export def render-testrun [testrun: string, profile: string, --set: record = {}]: string -> string {
    let named = $in
    | str replace --regex '(?m)(kind: TestRun\nmetadata:\n\s+name: )[^\n]+' (['${1}' $testrun] | str join '')
    let profiled = if ($profile | is-empty) {
        $named
    } else {
        $named | str replace --all 'value: load' $'value: ($profile)'
    }

    $set
    | items {|name, value|
        {
            name: $name
            value: $value
        }
    }
    | reduce --fold $profiled {|entry, text|
        $text | str replace --regex (['(?m)(- name: ' $entry.name '\n\s+value: ).*'] | str join '') (['${1}' $entry.value] | str join '')
    }
}

# Read one condition type from the k6 runner Jobs.
def job-condition [kubecfg: path, label: string, type: string]: nothing -> string {
    let path = [
        'jsonpath={.items[*].status.conditions[?(@.type=="'
        $type
        '")].status}'
    ] | str join ''

    wrap-kubectl $kubecfg -n (namespace) get jobs -l $label -o $path | str trim
}

# Return completion and failure flags for one k6 runner Job set.
def test-phase [kubecfg: path, label: string]: nothing -> record<complete: bool, failed: bool> {
    test-phase-from-status (job-condition $kubecfg $label Complete) (job-condition $kubecfg $label Failed)
}

# Create a k6 ConfigMap, run one TestRun, and print the runner output.
export def k6-run [
    kubecfg: path
    configmap: string
    script_key: string
    script_path: string
    testrun: string
    yaml_path: path
    --profile: string = ''
    --set: record = {}
]: nothing -> nothing {
    log info $'Creating configmap ($configmap)...'
    wrap-kubectl $kubecfg -n (namespace) create configmap $configmap --from-file=($script_key + '=' + $script_path) --from-file=lib.ts=k6/lib.ts --from-file=trigger.py=scripts/trigger.py --dry-run=client -o yaml
    | wrap-kubectl $kubecfg apply -f -

    log info $'Deleting previous testrun ($testrun)...'
    wrap-kubectl $kubecfg -n (namespace) delete testrun $testrun --ignore-not-found

    let raw = try {
        open --raw $yaml_path
    } catch {|err|
        fail $'Failed to open ($yaml_path): ($err.msg)' {
            command: k6-run
            span: (metadata $yaml_path).span
        }
    }
    let yaml = $raw | render-testrun $testrun $profile --set $set

    log info $'Applying testrun ($testrun)...'
    $yaml | wrap-kubectl $kubecfg apply -f -

    let label = $'k6_cr=($testrun),runner=true'
    log info 'Waiting for the runner job to appear...'
    if not (poll {|| is-runner-job-ready $kubecfg $label } {
        interval: 1sec
        max_attempts: 900
    }) {
        let stage = testrun-stage $kubecfg $testrun
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

    let pod = wrap-kubectl $kubecfg -n (namespace) get pods -l $label -o 'jsonpath={.items[0].metadata.name}'
    let runner_log = wrap-kubectl $kubecfg -n (namespace) logs $pod
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

# Run one E2E suite against one deployment mode.
export def run-suite [kubecfg: path, suite: string, mode: string]: nothing -> nothing {
    let testrun = $'data-proxy-e2e-($suite)-($mode)'
    log info $'Running the ($suite) suite against ($mode) mode...'
    k6-run $kubecfg data-proxy-e2e e2e.ts k6/e2e.ts $testrun k6/e2e.yaml --set {
        SUITE: $suite
        MODE: $mode
    }
}

# Switch to HA and back while validating both mode suites.
export def run-modes [kubecfg: path]: nothing -> nothing {
    switch-mode $kubecfg true
    run-suite $kubecfg modes ha
    switch-mode $kubecfg false
    run-suite $kubecfg modes single
}

# Run one k6 performance profile in the requested mode.
export def run-perf [kubecfg: path, profile: string, ha: bool]: nothing -> nothing {
    switch-mode $kubecfg $ha

    let failure = try {
        refresh-proxy $kubecfg
        clear-test-resources $kubecfg
        k6-run $kubecfg data-proxy-k6 perf.ts k6/perf.ts data-proxy-perf k6/perf.yaml --profile $profile --set {
            HA_MODE: (if $ha { 'true' } else { 'false' })
        }
        null
    } catch {|err|
        $err
    }

    if $failure != null {
        error make $failure.raw
    }
}
