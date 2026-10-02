
use ../lib.nu [fail]
use ./lib.nu [git-root wrap-kubectl wrap-minikube profile]

# Start a missing local Minikube cluster with supported capacity.
export def start-minikube [kubecfg: path]: nothing -> string {
    if (wrap-minikube $kubecfg status | complete).exit_code != 0 {
        wrap-minikube $kubecfg start --driver=podman --container-runtime=containerd --kubernetes-version v1.34.7 --cpus 6 --memory 12288 --disk-size 40g
    }

    wrap-minikube $kubecfg update-context
}

# Build and load every local platform image into Minikube.
export def --env build-images [kubecfg: path]: nothing -> nothing {
    let repo = git-root
    try {
        cd $repo
    } catch {|err|
        fail $'Could not change to repository root: ($err.msg)' {
            command: build-images
            span: (metadata $repo).span
        }
    }

    let images = [
        {
            image: 'data-proxy-sync:local'
            dockerfile: Dockerfile.sync
        }
        {
            image: 'localhost/data-proxy-postgres:17.0.0-local'
            dockerfile: Dockerfile.postgres
        }
        {
            image: 'data-proxy-proxy:local'
            dockerfile: Dockerfile.proxy
        }
        {
            image: 'data-proxy-jobs:local'
            dockerfile: Dockerfile.jobs
        }
        {
            image: 'localhost/k6:local'
            dockerfile: Dockerfile.k6
        }
    ]

    for image in $images {
        log info $'Building ($image.image)...'
        docker build -t $image.image -f $image.dockerfile .
        log info $'Loading ($image.image) into Minikube...'
        docker save $image.image | wrap-minikube $kubecfg image load -
    }
}

# Apply the local Google application-default credentials as a Kubernetes Secret.
export def apply-gcp-secret [kubecfg: path]: nothing -> nothing {
    let credentials = $env.HOME | path join .config/gcloud/application_default_credentials.json

    if not ($credentials | path exists) {
        log warning 'GCP credentials not found, skipping secret'
        return
    }

    wrap-kubectl $kubecfg -n data-proxy create secret generic gcp-key $'--from-file=key.json=($credentials)' --dry-run=client -o yaml
    | wrap-kubectl $kubecfg apply -f -
    | ignore
}

# Rebuild and roll out the local proxy image.
export def refresh-proxy [kubecfg: path]: nothing -> nothing {
    log info 'Building data-proxy-proxy:local...'
    docker build -q -t data-proxy-proxy:local -f Dockerfile.proxy .
    docker save -q data-proxy-proxy:local | wrap-minikube $kubecfg image load -
    wrap-kubectl $kubecfg -n data-proxy rollout restart deployment/data-proxy-proxy out> /dev/null
    wrap-kubectl $kubecfg -n data-proxy rollout status deployment/data-proxy-proxy --timeout=180s out> /dev/null
}
