use std/log
use lib.nu [
    fail
    git-root
    local-image-tag
    local-kubeconfig
    NAMESPACE
    wait-for
    wrap-helmfile
    wrap-kubectl
]

const RELEASE = 'data-proxy'

# Run helm against the local k3d kubeconfig.
def --wrapped wrap-helm [kubecfg: path, ...rest: string]: nothing -> string {
    with-env {KUBECONFIG: $kubecfg} {
        helm ...$rest
    }
}

# Run one SQL query in the primary and return the trimmed answer.
def query-primary [kubecfg: path, query: string]: nothing -> string {
    wrap-kubectl $kubecfg -n $NAMESPACE exec data-proxy-1 -c postgres -- psql -U postgres -d data-proxy -At -c $query
    | str trim
}

# Run a subset of the E2E scenarios against the current release.
def e2e-subset [scenarios: string]: nothing -> nothing {
    log info $'Running the E2E scenarios: ($scenarios)...'
    nu (git-root | path join scripts cluster.nu) k6 e2e --scenarios $scenarios
}

# Check what an upgrade must keep: the migrations, the rules, the logins, and the serving workloads.
def check-upgraded-release [kubecfg: path]: nothing -> nothing {
    let migrations = ls (git-root | path join console migrations) | where name =~ '\.sql$' | length
    let applied = query-primary $kubecfg 'SELECT max(version_id) FROM public.goose_db_version'
    let roles = query-primary $kubecfg 'SELECT count(*) FROM rbac.roles'
    let serving = query-primary $kubecfg "SELECT count(*) FROM pg_roles WHERE rolname = 'serving' AND rolcanlogin"

    let problems = [
        (if ($applied | into int) != $migrations { $'goose is at ($applied), and the repository has ($migrations) migrations' })
        (if ($roles | into int) == 0 { 'rbac.roles is empty' })
        (if ($serving | into int) != 1 { 'the serving login is missing or can not log in' })
    ]
    | compact

    for problem in $problems {
        fail $'The upgraded release is not right: ($problem)'
    }

    [
        data-proxy/data-proxy-pgdog
        data-proxy/data-proxy-postgrest
        data-proxy/data-proxy-sync
    ]
    | wait-for deployment $kubecfg
}

# Install a published chart release, upgrade it to the local chart, and check the upgraded release. Run it at release time, after the first release exists.
export def "main upgrade" [
    --from: string # Version of the published chart to start from, such as 1.127.0.
    --chart: string = 'oci://ghcr.io/prefeitura-rio/charts/data-proxy' # OCI address of the published chart.
]: nothing -> nothing {
    if ($from | is-empty) {
        fail 'Give the published chart version with --from'
    }

    let kubecfg = local-kubeconfig
    let repo = git-root
    cd $repo

    log info $'Removing the current release ($RELEASE)...'
    wrap-helm $kubecfg uninstall $RELEASE --namespace $NAMESPACE --wait --ignore-not-found | ignore
    wrap-kubectl $kubecfg -n $NAMESPACE delete pvc -l cnpg.io/cluster=$RELEASE --ignore-not-found | ignore

    log info $'Installing the published chart ($from)...'
    wrap-helm $kubecfg install $RELEASE $chart --version $from --namespace $NAMESPACE --values k3d/values/data-proxy.yaml --wait --wait-for-jobs --timeout 30m | ignore

    log info 'Upgrading to the local chart...'
    wrap-helmfile $kubecfg --image-tag (local-image-tag) sync

    check-upgraded-release $kubecfg
    e2e-subset 'roles,access,console,cache'
}
