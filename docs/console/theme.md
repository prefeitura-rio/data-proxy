# Theme

The app has the DaisyUI themes `light` and `dark`. It uses the theme of the system until the user selects the button in the navigation bar. The button saves the choice in the browser under the key `theme` in `localStorage`. A small script in `src/app.html` sets `data-theme` before the first paint, so the page doesn't flash. The notebook in the Query tab keeps its own theme.

New pages use the theme colors of DaisyUI, such as `bg-base-100` and `text-base-content`, and not fixed colors. Use `StatusBadge` for a status, `PageHeader` for the title and `Section` for a block with a heading.

The Access page lists the access policy of one schema and its access log, which the same `read_policy` permission opens. A sync run is visible to the user who started it from the first second. `console.start_sync` records the schemas of the sync run in the same transaction that queues it.

The route `/healthz` answers 200 without a session and calls nothing else. The chart uses it for the probes of the Deployment, and it sets resource requests and limits from `console.resources` and `notebooks.resources`.

| Setting | Contains |
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

The chart sets these settings ([Helm chart](../operations/ingress.md#console-and-notebooks)). In the k3d cluster, the realm `dev` has the client `data-proxy-console-web` with the secret `test-secret`. It has these users:

- `admin` (`dp_admin`), with the password `admin`.
- `web_viewer` (`dp_console`), `web_admin` (`dp_admin`), `web_api` (`dp_api`), `web_policy` (`dp_policy`), and `web_other` (`dp_admin` for the schema `other`), with the password `web-test`.

The client `console` of the realm is another client. The end-to-end suite uses it with client credentials.

<!-- nav:start -->
---

[Previous: Application](app.md) | [Next: Database views](views.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
