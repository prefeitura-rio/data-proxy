
use ../lib.nu [fail poll]
use ./lib.nu [git-root wrap-helm wrap-kubectl namespace wait-for]

# Return the display name for one deployment mode.
export def mode-name [ha: bool]: nothing -> string {
    if $ha {
        return 'HA'
    }

    'single'
}

# Return whether one CNPG instance count represents the requested deployment mode.
export def is-mode-matching [instances: int, ha: bool]: nothing -> bool {
    if $ha {
        return ($instances >= 2)
    }

    $instances == 1
}

# Return whether the CNPG Cluster is ready at the requested instance count.
export def is-cluster-settled [kubecfg: path, ha: bool]: nothing -> bool {
    let cluster = try {
        wrap-kubectl $kubecfg -n (namespace) get cluster data-proxy -o json | from json
    } catch {
        return false
    }
    let instances = $cluster.spec.instances

    (is-mode-matching $instances $ha)
    and ($cluster.status.readyInstances? == $instances)
    and ($cluster.status.phase? == 'Cluster in healthy state')
}

# Switch between single and HA mode and wait for required workloads.
export def switch-mode [kubecfg: path, ha: bool]: nothing -> nothing {
    let repo = git-root
    let mode = mode-name $ha

    let cluster = try {
        wrap-kubectl $kubecfg -n (namespace) get cluster data-proxy -o json | from json
    } catch {
        null
    }

    let matches = $cluster != null and (is-mode-matching $cluster.spec.instances $ha)

    if not $matches {
        log info $'Switching to ($mode) mode...'
        wrap-helm $kubecfg upgrade data-proxy $'($repo)/helm' --namespace (namespace) --values $'($repo)/scripts/values/data-proxy.yaml' --set $'ha.enabled=($ha)' --timeout 10m

        [
            'data-proxy/data-proxy-proxy'
            'data-proxy/data-proxy-postgrest'
            'data-proxy/data-proxy-sync'
        ] | wait-for deployment $kubecfg
    } else {
        log info $'Cluster already targets ($mode) mode; skipping Helm upgrade'
    }

    log info $'Waiting for the ($mode) instances...'
    if not (poll {|| is-cluster-settled $kubecfg $ha } {
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
