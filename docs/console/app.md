# Application

The console is a SvelteKit app. These sections describe the app, its lists, and the way it handles tokens from the identity provider.

## The web app

The app signs in people with OpenID Connect: the authorization code flow with PKCE and a `state` value. The server keeps the access token and the refresh token in an encrypted cookie. The cookie is `httpOnly`, `SameSite=Lax` and host-only.

The token is too long for one cookie, so the server splits the sealed value into pieces of 3500 characters. The server refreshes the token 60 seconds before it expires, and a failed refresh sends the user back to the sign-in page. Every request that changes data must come from the origin of the console, or the server answers 403.

`GET /rpc/permissions` decides which tabs and buttons the user sees, and the database still checks every call. A token with no `role` claim shows the no-access page. The database takes it for an anonymous request, and a new sign-in would give the same token. The routes are `/sync`, `/sync/runs/<id>`, `/config`, `/access` and `/query`.

Each tab needs one permission to open, and it shows an action only if the user has its permission:

| Tab | Route | Needs | Actions need | Data |
| --- | --- | --- | --- | --- |
| Sync | `/sync` | `read_sync` | `start_sync`, `control_sync` | The views `runs`, `tasks`, `steps`, `logs`, `errors`, `overview`, `queues` and `schedules`. |
| Config | `/config` | `read_config` | `create_config`, `edit_config`, `delete_config`, `import_config` | The tables `schemas`, `tables` and `rls`, the history, and the import preview. |
| Access | `/access` | `read_policy` | `create_policy`, `edit_policy`, `delete_policy` | `access_policy` and `access_log` of one schema. |
| Query | `/query` | `read_files` | None | The file lists of a table and the signed links. |

The app has these routes besides the tab pages. Every request that changes data also needs the console origin, as described above.

| Route | Method | Sign-in | Work |
| --- | --- | --- | --- |
| `/auth/login` | GET | No | Makes a PKCE pair and a `state`, stores them in a cookie for ten minutes, and redirects to the identity provider. The query `next` names the page to open after the sign-in. The default is `/sync`. |
| `/auth/callback` | GET | No | Checks the `state`, exchanges the code, writes the session cookie, and redirects to `next`. It accepts only a path on the console. It answers 400 when the answer doesn't match the request. |
| `/auth/logout` | POST | No | Deletes the session cookie. It redirects to the end-session address of the identity provider, or to `/auth/login` when the provider has none. |
| `/config/export` | GET | Yes | Downloads `sync.json`, which `console.sync_config()` builds from the rows that the user may read. |
| `/query/grant` | POST | Yes | Takes `{schema, tables}` and returns the signed links for the files of those tables. It needs `read_files` and the schema, which `console.table_files()` checks. It answers 503 when the query tab isn't set up. |
| `/sync/runs/<id>/logs` | GET | Yes | Streams the status and the new log lines of the run as server-sent events, one read each second, until the sync run finishes or the page leaves. The query `after`, or the header `Last-Event-ID` of a reconnect, limits the lines to a later time. The query `settle` keeps the stream open for 30 seconds after the status finishes, because a resume or a retry may follow. It answers 404 for an unknown run. |
| `/healthz` | GET | No | Answers `ok`. The probes of the Deployment use it. |

The code of the app is in `console/ui/src`:

- `lib/server/` contains the session, the OpenID Connect client, the PostgREST client, the catalog reader and the signer.
- `lib/types.ts` contains the types of the rows. Write the types by hand. Update them in the same commit that changes a view.

The Config page edits a row in a dialog:

- A schema has a `claim` and `encrypted`.
- A table has a strategy, `n`, `fallback`, a cache TTL (time to live) and sort columns.
- A row rule has a column and a unit type.

The page doesn't edit `partitioning`. Change it with an import of the document.

## Theme

The app has the DaisyUI themes `light` and `dark`. It uses the theme of the system until the user selects the button in the navigation bar. The button saves the choice in the browser under the key `theme` in `localStorage`. A small script in `src/app.html` sets `data-theme` before the first paint, so the page doesn't flash. The notebook in the Query tab keeps its own theme.

New pages use the theme colors of DaisyUI, such as `bg-base-100` and `text-base-content`, and not fixed colors. Use `StatusBadge` for a status, `PageHeader` for the title and `Section` for a block with a heading.

The Access page lists the access policy of one schema and its access log, which the same `read_policy` permission opens. A sync run is visible to the user who started it from the first second. `console.start_sync` records the schemas of the sync run in the same transaction that queues it.

The route `/healthz` answers 200 without a session and calls nothing else. The chart uses it for the probes of the Deployment, and it sets resource requests and limits from `console.resources` and `notebooks.resources`.

| Setting | Holds |
| --- | --- |
| `OIDC_ISSUER` | The address of the identity provider. |
| `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | A confidential client with the redirect URI `<origin>/auth/callback`. |
| `COOKIE_SECRET` | The secret that seals the cookie. |
| `POSTGREST_URL` | The address of PostgREST. |
| `ORIGIN` | The public origin of the console. The server compares the `Origin` header of every change with it. The server sets the `Secure` flag on its cookies when it starts with `https`. |
| `PROTOCOL_HEADER` | The header that contains the scheme to the Node adapter, `x-forwarded-proto` behind the gateway. Without it, the adapter assumes `https` and refuses every form post from an `http` site. |
| `NOTEBOOK_ORIGIN`, `CATALOG_URL`, `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | The query tab. Without them, the tab says that it isn't set up. `S3_REGION` is optional. |
| `GRANT_TTL_SECONDS` | Optional. The lifetime of the signed links in seconds. The default is 300. The page renews a grant at 80% of it. |
| `S3_ADMIN_ACCESS_KEY`, `S3_ADMIN_SECRET_KEY`, `S3_IAM_ENDPOINT` | Optional. They turn on one storage identity for each schema. The IAM endpoint is the storage endpoint by default. |

The chart sets these settings ([Helm chart](../operations/ingress.md#console-and-notebooks)). In the k3d cluster, the realm `dev` has the client `console-web` with the secret `test-secret`. It has these users:

- `admin` (`dp_admin`), with the password `admin`.
- `web_viewer` (`dp_console`), `web_admin` (`dp_admin`), `web_api` (`dp_api`), `web_policy` (`dp_policy`), and `web_other` (`dp_admin` for the schema `other`), with the password `web-test`.

The client `console` of the realm is another client. The end-to-end suite uses it with client credentials.

## Lists

Every list pages. A page has 25 rows. The access policy and the tasks and steps of a sync run have 50 rows each, and the errors have 10 rows. The footer says which rows the page shows and how many rows there are.

PostgREST returns the total in the `Content-Range` header when the request sends `Prefer: count=exact`. The address contains the page and the filters, so you can bookmark a page.

The runs list filters by status, by schema, by a range of dates and by the first characters of a sync run id. The schema filter uses the column `schemas` of the view `runs`. The errors list, the access policy, the access log, the tables and the history filter by text or by a value. The log of a sync run shows 500 lines, and a button loads 500 more, up to 5000.

## Tokens from the identity provider

The console sends the access token of the user to PostgREST. PostgREST checks it against the keys of the identity provider, like it does for the data API. The token needs these claims besides the standard ones:

| Claim | Holds |
| --- | --- |
| `role` | One role of `rbac.toml`, such as `dp_console`. It's the database role that PostgREST switches to. |
| `schemas` | The list of schemas that the user manages, such as `["sales", "hr"]`. |

The `preferred_username` claim names the user in `updated_by` and `started_by`.

<!-- nav:start -->
---

[Previous: Console](index.md) | [Next: Database views](views.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
