# Console variables

The console server reads these variables. The chart sets them from its values. See [Console and notebooks](./ingress.md#console-and-notebooks) for the values.

| Variable | Contains |
| --- | --- |
| `OIDC_ISSUER` | The address of the identity provider. |
| `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | A confidential client with the redirect URI `<origin>/console/auth/callback`. |
| `COOKIE_SECRET` | The secret that seals the cookie. |
| `POSTGREST_URL` | The address of PostgREST. |
| `ORIGIN` | The public origin of the API host, with no path. The server compares the `Origin` header of every change with it. The server sets the `Secure` flag on its cookies when it starts with `https`. |
| `PROTOCOL_HEADER` | The header that contains the scheme to the Node adapter, `x-forwarded-proto` behind the gateway. Without it, the adapter assumes `https` and refuses every form post from an `http` site. |
| `NOTEBOOK_ORIGIN`, `CATALOG_URL`, `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | The query tab. Without them, the tab says that it isn't set up. `S3_REGION` is optional. |
| `GRANT_TTL_SECONDS` | Optional. The lifetime of the signed links in seconds. The default is 300. The page renews a grant at 80% of it. |
| `S3_ADMIN_ACCESS_KEY`, `S3_ADMIN_SECRET_KEY`, `S3_IAM_ENDPOINT` | Optional. They turn on one storage identity for each schema. The IAM endpoint is the storage endpoint by default. |

<!-- nav:start -->
---

[Previous: Proxy variables](./proxy.md) | [Next: KEDA Scaling](./keda.md) | [Index](./index.md) | [Home](../index.md)
<!-- nav:end -->
