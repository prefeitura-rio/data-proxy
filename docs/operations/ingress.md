# Ingress

The chart exposes the console, the notebooks and the API through the Istio gateway.

## Console and notebooks

`console.enabled` and `notebooks.enabled` add the web console and the static site of the notebooks. Each one has a Deployment, a Service and a hostname on the Istio gateway.

Both use the image `console.image`, and the notebooks Deployment starts it with another command. The two hostnames must be different sites, so that a notebook never shares cookies with the console.

| Value | Holds |
| --- | --- |
| `console.host`, `console.scheme` | The public origin of the console. |
| `console.oidc.issuer`, `console.oidc.clientId`, `console.oidc.clientSecret` | The confidential client of the identity provider. `console.oidc.existingSecret` names a Secret with `OIDC_CLIENT_SECRET` and `COOKIE_SECRET` instead. |
| `console.storage.endpoint` | The storage endpoint that the signed links use. The browser must reach it. The notebook policy allows only this host. |
| `console.storage.existingSecret` | A Secret with `accessKey`, `secretKey`, and optionally `schemaCredentials` with a JSON object of credentials limited to one schema. Without it, the console signs with the read-only credential of the chart. |
| `notebooks.host`, `notebooks.scheme` | The public origin of the notebooks. |
| `console.resources`, `notebooks.resources` | The resource requests and limits of the two Deployments. |
| `s3.bootstrap.corsOrigins` | The origins that the cross-origin resource sharing (CORS) rule of the bucket allows. Set it to the notebook origin. The bootstrap Job sets the rule through the S3 API, and it skips a GCS endpoint. |
| `console.storage.perSchemaIdentities`, `console.storage.adminSecret`, `console.storage.iamEndpoint` | One storage identity for each schema, made by the console with the admin credential of `adminSecret` or of the S3 secret of the chart. SeaweedFS needs the flag `-s3.iam.readOnly=false` (`seaweedfs.allInOne.extraArgs`). |
| `auth.signerRole` | The login that the console uses to read the catalog. |

The chart makes a read-only identity next to the admin identity of the S3 secret, in the keys `S3_READER_ACCESS_KEY` and `S3_READER_SECRET_KEY`, and in `seaweedfs_s3_config`. `configure-db` creates the signer login, and its password is in the Secret `<release>-signer`.

## Istio ingress

Set `ingress.enabled` to create the VirtualService. Set `ingress.auth.enabled` to create JSON Web Token (JWT) authentication and authorization resources. Create the configured Gateway before installing the chart.

<!-- nav:start -->
---

[Previous: Initialization](initialization.md) | [Next: Topology](topology.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
