
use ../lib.nu [fail]
use ./lib.nu [wrap-kubectl wrap-minikube namespace profile wait-for wait-until]

# Return the total restart count for Pods selected in one namespace.
export def restart-count [kubecfg: path, target_namespace: string, selector: string]: nothing -> int {
    let pods = try {
        wrap-kubectl $kubecfg -n $target_namespace get pods -l $selector -o json
        | from json
        | get items
    } catch {
        return 0
    }

    if ($pods | is-empty) {
        return 0
    }

    $pods
    | each {|pod|
        $pod.status.containerStatuses
        | default []
        | each {|container| $container.restartCount | into int }
        | math sum
    }
    | math sum
}

# Return whether a Service has a ready EndpointSlice address.
export def is-service-ready [kubecfg: path, target_namespace: string, service: string]: nothing -> bool {
    let slices = try {
        wrap-kubectl $kubecfg -n $target_namespace get endpointslice -l $'kubernetes.io/service-name=($service)' -o json
        | from json
        | get items
    } catch {
        return false
    }

    $slices | any {|slice|
        $slice.endpoints
        | default []
        | any {|endpoint| $endpoint.conditions.ready }
    }
}

# Return controller restart counts keyed by controller name.
def controller-restart-counts [kubecfg: path, ...controllers: record<name: string, namespace: string, selector: string>]: nothing -> record {
    $controllers
    | each {|controller|
        [
            $controller.name
            (restart-count $kubecfg $controller.namespace $controller.selector)
        ]
    }
    | into record
}

# Return names of controllers whose restart counts changed during a stability window.
export def restarted-controller-names [before: record, after: record]: nothing -> list<string> {
    $after
    | transpose name count
    | where ($it.count != ($before | get --optional $it.name | default (-1)))
    | get name
}

# Wait until the Kubernetes API and storage provisioner are stable.
export def wait-for-control-plane [kubecfg: path]: nothing -> nothing {
    if not (wait-until {|| ((wrap-kubectl $kubecfg get --raw /readyz | complete).exit_code == 0) } {
        interval: 5sec
        max_attempts: 60
    }) {
        fail "Kubernetes API server didn't become ready within 5 minutes" {
            command: wait-for-control-plane
            span: (metadata $kubecfg).span
        }
    }

    wrap-kubectl $kubecfg -n kube-system wait --for=condition=Ready pod -l component=etcd --timeout=5m | ignore
    wrap-kubectl $kubecfg -n kube-system wait --for=condition=Ready pod/storage-provisioner --timeout=5m | ignore

    let before = restart-count $kubecfg kube-system 'integration-test=storage-provisioner'
    sleep 30sec
    let after = restart-count $kubecfg kube-system 'integration-test=storage-provisioner'

    if $after != $before {
        fail 'storage-provisioner restarted during the control-plane stability window' {
            command: wait-for-control-plane
            span: (metadata $kubecfg).span
        }
    }
}

# Wait until the resource metrics API is available.
export def wait-for-metrics [kubecfg: path]: nothing -> nothing {
    ['kube-system/metrics-server'] | wait-for deployment $kubecfg | ignore

    if not (wait-until {|| ((wrap-kubectl $kubecfg get --raw /apis/metrics.k8s.io/v1beta1/nodes | complete).exit_code == 0) } {
        interval: 5sec
        max_attempts: 60
    }) {
        fail "metrics-server didn't become ready within 5 minutes" {
            command: wait-for-metrics
            span: (metadata $kubecfg).span
        }
    }
}

# Verify that platform controllers and webhooks remain stable.
export def verify-platform [kubecfg: path]: nothing -> nothing {
    [
        'cnpg-system/cnpg-cloudnative-pg'
        'keda/keda-operator'
        'keda/keda-operator-metrics-apiserver'
        'keda/keda-admission-webhooks'
    ] | wait-for deployment $kubecfg | ignore

    for webhook in [
        {
            namespace: cnpg-system
            name: cnpg-webhook-service
        }
        {
            namespace: keda
            name: keda-admission-webhooks
        }
    ] {
        if not (is-service-ready $kubecfg $webhook.namespace $webhook.name) {
            fail $'Webhook service ($webhook.namespace)/($webhook.name) has no endpoint' {
                command: verify-platform
                span: (metadata $kubecfg).span
            }
        }
    }

    let controllers = [
        {
            name: cnpg
            namespace: cnpg-system
            selector: 'app.kubernetes.io/name=cloudnative-pg'
        }
        {
            name: keda
            namespace: keda
            selector: 'app.kubernetes.io/name=keda-operator'
        }
        {
            name: keda-metrics
            namespace: keda
            selector: 'app.kubernetes.io/name=keda-metrics-apiserver'
        }
        {
            name: keda-webhook
            namespace: keda
            selector: 'app.kubernetes.io/name=keda-admission-webhooks'
        }
        {
            name: storage
            namespace: kube-system
            selector: 'integration-test=storage-provisioner'
        }
    ]
    let before = controller-restart-counts $kubecfg ...$controllers

    sleep 30sec

    let after = controller-restart-counts $kubecfg ...$controllers
    let restarted = restarted-controller-names $before $after

    if ($restarted | is-not-empty) {
        fail $'Controllers restarted during the platform stability window: ($restarted | str join ", ")' {
            command: verify-platform
            span: (metadata $kubecfg).span
        }
    }
}

# Print the local cluster status with kubecolor.
export def show-status [kubecfg: path]: nothing -> nothing {
    with-env {
        KUBECONFIG: $kubecfg
    } {
        kubecolor --context=(profile) -n (namespace) get pods
        print ''
        kubecolor --context=(profile) -n (namespace) get deploy
        print ''
        kubecolor --context=(profile) -n (namespace) get cluster
        print ''
        kubecolor --context=(profile) -n (namespace) get scaledobject
        print ''
        kubecolor --context=(profile) -n (namespace) get scaledjob
    }
}

# Return whether the primary CNPG Cluster reports a healthy phase.
export def is-cnpg-healthy [kubecfg: path]: nothing -> bool {
    let phase = try {
        wrap-kubectl $kubecfg -n (namespace) get cluster data-proxy -o json
        | from json
        | get status.phase
    } catch {
        ''
    }

    $phase == 'Cluster in healthy state'
}
