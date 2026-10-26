# Hardening

This page describes how the chart limits what a pod, a network peer and an image can do. Each default is a value that you can override.

## Pods

Every container drops all Linux capabilities, forbids privilege escalation and uses the default seccomp profile. Every pod must run as a non-root user. The chart sets a numeric user for each container, because Kubernetes can't check a user that has only a name.

| Component | User | Read-only root file system | Reason for a difference |
| --- | --- | --- | --- |
| Console, notebooks | 1000 | Yes | |
| PostgREST, PgDog | 1000 | Yes | |
| Proxy | 10001 | Yes | nginx writes the file with its process number and its temporary files to `/tmp`, an `emptyDir` volume. |
| Sync worker | 10001 | No | DuckDB and Python write to the home folder. |
| Jobs image | 1000 | No | Nushell writes its history and its settings to the home folder. |
| Swagger UI | root | No | nginx writes its settings and its cache, and then it runs the workers as its own user. The container keeps only the capabilities that this needs. |

The values `securityContext.pod` and `securityContext.container` have the defaults. The values `securityContext.components.<name>` replace a default for one component. An init container of a pod uses the settings of its own image, so a `wait-for-postgres` step runs as the user of the Jobs image.

## Network policies

The chart doesn't render a network policy by default. Set `networkPolicy.enabled` to `true` to limit the traffic that comes into the pods. Your network plugin must enforce policies. The policies don't limit the traffic that leaves a pod.

| Pods | Port | Allowed sources |
| --- | --- | --- |
| Proxy | 8080 | The Istio ingress gateway |
| Proxy | 8081 (admin) | The sync workers |
| PostgREST | 3000 | The proxy and the console |
| PostgREST | metrics port | The monitoring namespace |
| PgDog | 6432 | PostgREST |
| PgDog | 9090 (metrics) | The monitoring namespace |
| Swagger UI, console, notebooks | The port of the pod | The Istio ingress gateway |

Three values set the sources:

- `networkPolicy.gatewayNamespace` is the namespace of the gateway pods. The default is `istio-ingress`.
- `networkPolicy.monitoringNamespace` is the namespace that reads the metrics. An empty value opens no metrics port.
- `networkPolicy.extraPeers` is a list of more peers for the proxy and PostgREST. Add the pods of a load test here.

Redis and the database are outside the chart. Limit them in the repository that deploys them.

## Console headers

The console sends a content security policy on each page. The policy allows only scripts, styles, images and connections of the console itself. SvelteKit adds a hash or a nonce for its own inline script.

The hook of the console adds two sources that only the settings know. The notebook address joins `frame-src`, and the address of the identity provider joins `form-action`, because the sign-out form leads there. The hook also sets `X-Content-Type-Options`, `Referrer-Policy: same-origin` and `Cache-Control: no-store`. A route that sets its own `Cache-Control` keeps it.

Every browser scenario fails if the browser reports a refusal under the policy.

## Images

The chart pins PostgREST, PgDog and Swagger UI by tag and digest. Each `Containerfile` pins its base images the same way. The digest decides which image runs, and the tag tells you the version.

To update an image, read the digest of the new tag from its registry, and change the tag and the digest in one commit.

CI signs each image that it pushes with a keyless cosign signature. It also attaches a provenance record and a software bill of materials. To check a signature, run:

```bash
cosign verify ghcr.io/prefeitura-rio/data-proxy-sync:<tag> \
  --certificate-identity-regexp 'https://github.com/prefeitura-rio/data-proxy/.github/workflows/ci.yaml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

<!-- nav:start -->
---

[Previous: Topology](topology.md) | [Next: Environment Variables](environment.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
