# nu-lint-ignore-file: dont_mix_different_effects, max_positional_params, string_may_be_bare, division_to_format_duration, remove_hat_not_builtin, unhandled_external_error

use std/log

const PROFILE = 'data-proxy'

# Path to the repository git-root.
def git-root []: nothing -> string {
    git rev-parse --show-toplevel | str trim
}

# Wrapped kubectl using the isolated kubeconfig.
def --wrapped k [kubecfg: path, ...rest: string]: string -> string, nothing -> string {
    with-env {KUBECONFIG: $kubecfg} {
        kubectl --context=($PROFILE) ...$rest
    }
}

# Wrapped minikube for the data-proxy profile with the isolated kubeconfig.
def --wrapped mk [kubecfg: path, ...rest: string]: nothing -> string {
    with-env {KUBECONFIG: $kubecfg} {
        minikube --profile $PROFILE ...$rest
    }
}

# Wrapped helm using the isolated kubeconfig.
def --wrapped hm [kubecfg: path, ...rest: string]: nothing -> string {
    with-env {KUBECONFIG: $kubecfg} {
        ^helm --kube-context $PROFILE ...$rest
    }
}

# Wait for the given resources to roll out.
def wait-for [kind: string, kubecfg: path]: list<string> -> nothing {
    for ref in $in {
        let r = $ref | parse '{namespace}/{name}' | first
        log info $'  ($kind)/($r.namespace)/($r.name)…'
        (k
            $kubecfg
            -n
            $r.namespace
            rollout
            status
            $'($kind)/($r.name)'
            --timeout=15m
        )
    }
}

# Start Minikube if it is not already running.
def start-minikube [kubecfg: path]: nothing -> string {
    if (mk $kubecfg status | complete).exit_code != 0 {
        (
            (mk
                $kubecfg
                start
                --driver=podman
                --container-runtime=containerd
                --cpus=6
                --memory=12288
                --disk-size=40g
            )
        )
    }

    mk $kubecfg update-context
}

# Build the platform container images into Minikube.
def --env build-images [kubecfg: path]: nothing -> string {
    let repo = git-root
    try { cd $repo } catch {|err|
        log error $'cd failed: ($err.msg)'
        return
    }

    [
        {image: 'localhost/data-proxy-postgres:17.0.0-local', dockerfile: 'Dockerfile.postgres'}
        {image: 'data-proxy-nginx-proxy:local', dockerfile: 'Dockerfile.proxy'}
        {image: 'data-proxy-nushell:local', dockerfile: 'Dockerfile.nushell'}
        {image: 'localhost/k6:local', dockerfile: 'Dockerfile.k6'}
        {image: 'localhost/oidc:local', dockerfile: 'Dockerfile.oidc'}
    ]
    | each {|img|
        log info $'Building ($img.image)…'
        docker build -t $img.image -f $img.dockerfile .
        log info $'Loading ($img.image) into Minikube…'
        docker save $img.image | mk $kubecfg image load -
    }
}

# Apply the GCP service-account key as a Kubernetes secret.
def apply-gcp-secret [kubecfg: path]: nothing -> string {
    let creds = $env.HOME | path join .config/gcloud/application_default_credentials.json

    if not ($creds | path exists) {
        log warning 'GCP credentials not found, skipping secret'
        return
    }

    (
        k $kubecfg -n data-proxy create secret generic gcp-key $'--from-file=key.json=($creds)' --dry-run=client -o yaml
    )
    | k $kubecfg apply -f -
}

# Create the local Redis connection Secret consumed by data-proxy.
def provision-gcp-key [kubecfg: path]: nothing -> nothing {
    let creds = $env.HOME | path join .config/gcloud/application_default_credentials.json
    if not ($creds | path exists) {
        log warning 'GCP credentials not found, skipping CNPG key provisioning'
        return
    }

    let pod = (
        (k
            $kubecfg
            -n
            data-proxy
            get
            pod
            -l
            cnpg.io/cluster=data-proxy
            -l
            cnpg.io/instanceRole=primary
            -o
            'jsonpath={.items[0].metadata.name}'
        )
        | str trim
    )
    kubectl --kubeconfig=($kubecfg) --context=data-proxy -n data-proxy cp $creds $'($pod):/var/lib/postgresql/data/gcp-key.json'
    let size = (
        (k
            $kubecfg
            -n
            data-proxy
            exec
            $pod
            --
            wc
            -c
            /var/lib/postgresql/data/gcp-key.json
        )
        | str trim
    )
    if $size =~ ': 0' {
        error make {
            msg: 'Copied GCP credential is empty'
            label: {
                text: 'provision-gcp-key'
                span: (metadata $size).span
            }
        }
    }
}

# Run weed shell commands inside the SeaweedFS all-in-one pod.
def weed [kubecfg: path, ...commands: string]: nothing -> nothing {
    let jsonpath = 'jsonpath={.items[0].metadata.name}'

    let pod = (
        (k
            $kubecfg
            -n
            data-proxy
            get
            pod
            -l
            app.kubernetes.io/component=seaweedfs-all-in-one
            -o
            $jsonpath
        )
        | str trim
    )

    ($commands
        | str join (char nl)
        | kubectl --kubeconfig=($kubecfg) --context=($PROFILE) -n data-proxy exec -i $pod -- weed shell -master=localhost:9333
    ) | ignore
}

# Empty the SeaweedFS bucket and clear Redis and Postgres so the next k6 test
# starts from a clean baseline.
def clear-test-resources [kubecfg: path]: nothing -> nothing {
    (weed
        $kubecfg
        's3.bucket.delete -name test-bucket'
        's3.bucket.create -name test-bucket'
    )

    let stale_jobs = (
        k $kubecfg -n data-proxy get jobs -o name
        | lines
        | where $it =~ 'seeder|publisher'
    )

    if ($stale_jobs | is-not-empty) {
        k $kubecfg -n data-proxy delete ...$stale_jobs --ignore-not-found
    }

    let jsonpath = 'jsonpath={.items[0].metadata.name}'
    let valkey = (
        (k $kubecfg -n data-proxy get pod -l app.kubernetes.io/name=valkey -o $jsonpath)
        | str trim
    )

    for stream in [dp:extract dp:prepare dp:publish] {
        (k
            $kubecfg
            -n
            data-proxy
            exec
            $valkey
            --
            redis-cli
            XTRIM
            $stream
            MAXLEN
            0
        )
    }

    (k
        $kubecfg
        -n
        data-proxy
        exec
        $valkey
        --
        sh
        -c
        'redis-cli --scan --pattern "dp:state:*" | xargs -r redis-cli DEL; redis-cli --scan --pattern "dp:plans:*" | xargs -r redis-cli DEL; redis-cli --scan --pattern "dp:results:*" | xargs -r redis-cli DEL; redis-cli --scan --pattern "dp:remaining:*" | xargs -r redis-cli DEL; redis-cli DEL dp:active'
    )

    let duckdb = (k
        $kubecfg
        -n
        data-proxy
        get
        pod
        -l
        cnpg.io/cluster=data-proxy
        -l
        cnpg.io/instanceRole=primary
        -o
        $jsonpath
    ) | str trim

    let tables = (
        k $kubecfg -n data-proxy exec $duckdb -- psql postgresql://dataproxy:test-pg-pass@data-proxy-rw:5432/dataproxy -t -A -c "SELECT tablename FROM pg_tables WHERE schemaname = 'pic' AND tablename NOT IN ('freshness', 'access_policy')"
    )

    if ($tables | str trim | is-not-empty) {
        let drop_stmt = (
            $tables
            | lines
            | each {|t| $'DROP TABLE IF EXISTS pic."($t | str trim)" CASCADE' }
            | str join '; '
        )
        (
            k $kubecfg -n data-proxy exec $duckdb -- psql postgresql://dataproxy:test-pg-pass@data-proxy-rw:5432/dataproxy -c $"($drop_stmt); DELETE FROM partman.part_config WHERE parent_table LIKE 'pic.%'; DELETE FROM pic.freshness; DELETE FROM pic.access_policy;"
        )
    } else {
        (
            k $kubecfg -n data-proxy exec $duckdb -- psql postgresql://dataproxy:test-pg-pass@data-proxy-rw:5432/dataproxy -c "DELETE FROM partman.part_config WHERE parent_table LIKE 'pic.%'; DELETE FROM pic.freshness; DELETE FROM pic.access_policy;"
        )
    }
}

# Create a configmap, apply a testrun yaml, wait for completion, and print the runner log.
def k6-run [
    kubecfg: path
    configmap: string
    script_key: string
    script_path: string
    testrun: string
    yaml_path: path
    --profile: string = ''
]: nothing -> nothing {
    log info $'Creating configmap ($configmap)…'
    (k
        $kubecfg
        -n
        data-proxy
        create
        configmap
        $configmap
        --from-file=($script_key + '=' + $script_path)
        --from-file=lib.ts=k6/lib.ts
        --dry-run=client
        -o
        yaml
    ) | k $kubecfg apply -f -

    log info $'Deleting previous testrun ($testrun)…'
    k $kubecfg -n data-proxy delete testrun $testrun --ignore-not-found

    log info $'Applying testrun ($testrun)…'
    if $profile != '' {
        let yaml = try { open --raw $yaml_path } catch {|err| error make {
            msg: $'Failed to open ($yaml_path): ($err.msg)'
            label: {
                text: $yaml_path
                span: (metadata $yaml_path).span
            }
        } }

        $yaml
        | str replace --all 'value: load' $'value: ($profile)'
        | k $kubecfg apply -f -
    } else {
        k $kubecfg apply -f $yaml_path
    }

    let label = $'k6_cr=($testrun),runner=true'
    let items_jsonpath = 'jsonpath={.items}'
    let complete_jsonpath = '{range .items[*]}{.status.conditions[?(@.type=="Complete")].status}{.status.conditions[?(@.type=="Failed")].status}{end}'
    let pod_jsonpath = 'jsonpath={.items[0].metadata.name}'

    log info 'Waiting for the runner job to appear…'
    while true {
        let jobs = k $kubecfg -n data-proxy get jobs -l $label -o $items_jsonpath | str trim
        if ($jobs | is-not-empty) and ($jobs != '[]') { break }
        sleep 1sec
    }

    log info 'Waiting for the test to complete…'
    while true {
        let phase = (
            (k $kubecfg -n data-proxy get jobs -l $label -o $'jsonpath=($complete_jsonpath)')
            | str trim
        )
        if $phase =~ True { break }
        sleep 2sec
    }

    let pod = (k $kubecfg -n data-proxy get pods -l $label -o $pod_jsonpath)
    let runner_log = (k $kubecfg -n data-proxy logs $pod)

    print ($runner_log | to text)
}

# Print cluster status with kubecolor.
def show-status [kubecfg: path]: nothing -> nothing {
    with-env {KUBECONFIG: $kubecfg} {
        kubecolor --context=($PROFILE) -n data-proxy get pods
        print ""
        kubecolor --context=($PROFILE) -n data-proxy get deploy
        print ""
        kubecolor --context=($PROFILE) -n data-proxy get cluster
        print ""
        kubecolor --context=($PROFILE) -n data-proxy get scaledobject
        print ""
        kubecolor --context=($PROFILE) -n data-proxy get scaledjob
    }
}

# Rebuild and roll out the local nginx proxy image.
def refresh-proxy [kubecfg: path]: nothing -> nothing {
    log info 'Building data-proxy-nginx-proxy:local…'
    docker build -q -t data-proxy-nginx-proxy:local -f Dockerfile.proxy .
    docker save -q data-proxy-nginx-proxy:local | mk $kubecfg image load -
    k $kubecfg -n data-proxy rollout restart deployment/data-proxy-nginx-proxy out> /dev/null
    (k
        $kubecfg
        -n
        data-proxy
        rollout
        status
        deployment/data-proxy-nginx-proxy
        --timeout=180s
    ) out> /dev/null
}

# Run the standard k6 load profile.
def "main k6 load" []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig
    refresh-proxy $kubecfg
    clear-test-resources $kubecfg
    k6-run $kubecfg 'data-proxy-k6' 'load.ts' 'k6/load.ts' 'data-proxy-load' 'k6/load.yaml' --profile 'load'
}

# Run the k6 stress profile.
def "main k6 stress" []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig
    refresh-proxy $kubecfg
    clear-test-resources $kubecfg
    k6-run $kubecfg 'data-proxy-k6' 'load.ts' 'k6/load.ts' 'data-proxy-load' 'k6/load.yaml' --profile 'stress'
}

# Run the e2e test (triggers sync, seeds RLS, validates pipeline).
def "main k6 e2e" []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig

    let repo = git-root
    try { cd $repo } catch {|err|
        log error $'cd failed: ($err.msg)'
        return
    }

    log info 'Building data-proxy:local…'
    docker build -q -t data-proxy:local -f Dockerfile .
    docker save -q data-proxy:local | mk $kubecfg image load -

    log info 'Building data-proxy-nginx-proxy:local…'
    docker build -q -t data-proxy-nginx-proxy:local -f Dockerfile.proxy .
    docker save -q data-proxy-nginx-proxy:local | mk $kubecfg image load -

    log info 'Waiting for the CNPG controller…'
    ['cnpg-system/cnpg-cloudnative-pg'] | wait-for deployment $kubecfg

    log info 'Applying GCP secret…'
    apply-gcp-secret $kubecfg

    log info 'Deleting the init-db Job so it recreates the pgduckdb S3 secret…'
    k $kubecfg -n data-proxy delete job data-proxy-init-db --ignore-not-found

    log info 'Upgrading data-proxy release…'
    (hm
        $kubecfg
        upgrade
        --force-conflicts
        data-proxy
        $'($repo)/helm'
        --namespace
        data-proxy
        --values
        $'($repo)/scripts/values/data-proxy.yaml'
    )

    log info 'Provisioning GCP key for CNPG…'
    provision-gcp-key $kubecfg

    log info 'Waiting for the init-db Job…'
    (k
        $kubecfg
        -n
        data-proxy
        wait
        --for=condition=complete
        job/data-proxy-init-db
        --timeout=180s
    ) out> /dev/null

    log info 'Clearing test resources…'
    clear-test-resources $kubecfg

    log info 'Running e2e test…'
    (k6-run
        $kubecfg
        'data-proxy-e2e'
        'e2e.ts'
        'k6/e2e.ts'
        'data-proxy-e2e'
        'k6/e2e.yaml'
    )
}

# Start Minikube and install the complete local stack.
def "main up" []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig

    let repo = git-root

    log info 'Starting Minikube…'
    start-minikube $kubecfg

    k $kubecfg wait --for=condition=Ready nodes --all --timeout=5m

    log info 'Building container images…'
    build-images $kubecfg

    log info 'Building data-proxy:local…'
    docker build -t data-proxy:local -f ($repo | path join Dockerfile) $repo
    log info 'Loading data-proxy:local into Minikube…'
    docker save data-proxy:local | mk $kubecfg image load -

    log info 'Building Helm dependencies…'
    hm $kubecfg dependency build $'($repo)/helm'

    k $kubecfg create namespace data-proxy --dry-run=client -o yaml | k $kubecfg apply -f -
    log info 'Applying local Redis secret…'
    (
        k $kubecfg -n data-proxy create secret generic data-proxy-redis
            '--from-literal=REDIS_PASSWORD=valkey-local'
            '--from-literal=password=valkey-local'
            '--from-literal=REDIS={"read":"redis://:valkey-local@data-proxy-valkey:6379/1","write":"redis://:valkey-local@data-proxy-valkey:6379/0"}'
            --dry-run=client -o yaml
    ) | k $kubecfg apply -f -

    log info 'Applying local webdis ConfigMap…'
    (
        k $kubecfg -n data-proxy create configmap data-proxy-webdis
            '--from-literal=webdis-write.json={"redis_host":"data-proxy-valkey-0.data-proxy-valkey-headless.data-proxy.svc.cluster.local","redis_port":6379,"redis_auth":"__VALKEY_PASSWORD__","database":1,"http_port":7379,"daemonize":false,"logfile":"/dev/stdout"}'
            '--from-literal=webdis-read.json={"redis_host":"data-proxy-valkey.data-proxy.svc.cluster.local","redis_port":6379,"redis_auth":"__VALKEY_PASSWORD__","database":1,"http_port":7380,"daemonize":false,"logfile":"/dev/stdout"}'
            --dry-run=client -o yaml
    ) | k $kubecfg apply -f -

    log info 'Installing platform charts with Helmfile…'
    with-env {KUBECONFIG: $kubecfg} {
        helmfile --concurrency 1 --file ($repo | path join helmfile.yaml) sync --wait --timeout 900
    }

    log info 'Applying GCP secret…'
    apply-gcp-secret $kubecfg

    log info 'Installing data-proxy…'
    (hm
        $kubecfg
        upgrade
        --force-conflicts
        --install
        data-proxy
        $'($repo)/helm'
        --namespace
        data-proxy
        --values
        $'($repo)/scripts/values/data-proxy.yaml'
    )

    log info 'Waiting for data-proxy deployments…'
    [
        data-proxy/oidc
        data-proxy/data-proxy-swagger-ui
    ] | wait-for deployment $kubecfg

    log info 'Waiting for CNPG cluster…'
    while true {
        let phase = (
            (k
                $kubecfg
                -n
                data-proxy
                get
                cluster
                data-proxy
                -o
                'jsonpath={.status.phase}'
            )
            | str trim
        )
        if $phase == 'Cluster in healthy state' { break }
        sleep 2sec
    }

    log info 'Creating the SeaweedFS test bucket…'
    (weed
        $kubecfg
        's3.bucket.delete -name test-bucket'
        's3.bucket.create -name test-bucket'
    )

    show-status $kubecfg
}

# Remove the Minikube profile.
def "main down" []: nothing -> nothing {
    log info 'Deleting Minikube profile…'
    minikube --profile $PROFILE delete
}

# Script to create a testing environment with minikube
def main []: nothing -> nothing {
    let kubecfg = git-root | path join .kubeconfig

    mk $kubecfg status

    show-status $kubecfg
}
