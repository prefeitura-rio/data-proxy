# Ingress

The chart exposes the console, the notebooks and the API through the Istio gateway.

## Console and notebooks

`console.enabled` and `notebooks.enabled` add the web console and the static site of the notebooks. Each one has a Deployment and a Service.

The console answers on the `/console` path of `ingress.host`, the host of the API. The notebooks have their own host. A notebook is code that a user can edit, so it must never share an origin with the console.

Both use the image `console.image`, and the notebooks Deployment starts it with another command.

| Value | Holds |
| --- | --- |
| `ingress.host`, `ingress.scheme` | The public origin of the API, the docs and the console. |
| `console.oidc.issuer`, `console.oidc.clientId`, `console.oidc.clientSecret` | The confidential client of the identity provider. `console.oidc.existingSecret` names a Secret with `OIDC_CLIENT_SECRET` and `COOKIE_SECRET` instead. |
| `console.storage.endpoint` | The storage endpoint that the signed links use. The browser must reach it. The notebook policy allows only this host. |
| `console.storage.existingSecret` | A Secret with `accessKey`, `secretKey`, and optionally `schemaCredentials` with a JSON object of credentials limited to one schema. Without it, the console signs with the read-only credential of the chart. |
| `notebooks.host` | The public host of the notebooks. It uses `ingress.scheme`. |
| `console.resources`, `notebooks.resources` | The resource requests and limits of the two Deployments. |
| `s3.bootstrap.corsOrigins` | The origins that the cross-origin resource sharing (CORS) rule of the bucket allows. Set it to the notebook origin. The bootstrap Job sets the rule through the S3 API, and it skips a GCS endpoint. |
| `console.storage.perSchemaIdentities`, `console.storage.adminSecret`, `console.storage.iamEndpoint` | One storage identity for each schema, made by the console with the admin credential of `adminSecret` or of the S3 secret of the chart. SeaweedFS needs the flag `-s3.iam.readOnly=false` (`seaweedfs.allInOne.extraArgs`). |
| `auth.signerRole` | The login that the console uses to read the catalog. |

The chart makes a read-only identity next to the admin identity of the S3 secret, in the keys `S3_READER_ACCESS_KEY` and `S3_READER_SECRET_KEY`, and in `seaweedfs_s3_config`. `configure-db` creates the signer login, and its password is in the Secret `<release>-signer`.

## Addresses

Each environment has one host for the API, the docs and the console. The preview uses this layout now. Staging and production keep their old hosts until they move to this chart.

Only the preview has notebooks. They use their own host, `notebooks.data-proxy.dados.rio`.

| Environment | API | Docs | Console | Notebooks |
| --- | --- | --- | --- | --- |
| Production | `data-proxy.dados.rio` | `/docs` | `/console` | None yet |
| Staging | `stg.data-proxy.dados.rio` | `/docs` | `/console` | None yet |
| Preview | `preview.data-proxy.dados.rio` | `/docs` | `/console` | `notebooks.data-proxy.dados.rio` |

A wildcard certificate and a wildcard DNS record for `*.data-proxy.dados.rio` cover every name, because each name has one label before `data-proxy.dados.rio`. A table named `docs` or `console` in a schema isn't reachable on the API host.

The client of the console in the identity provider needs three addresses. The redirect address is `<origin>/console/auth/callback`. The post-logout address is `<origin>/console/auth/login`. The web origin is `<origin>`.

## Istio ingress

Set `ingress.enabled` to create the VirtualService. Set `ingress.auth.enabled` to create JSON Web Token (JWT) authentication and authorization resources. Create the configured Gateway before installing the chart.

<!-- nav:start -->
---

[Previous: Initialization](initialization.md) | [Next: Topology](topology.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
