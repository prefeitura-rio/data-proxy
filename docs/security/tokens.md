# Tokens

A token is valid until it expires. These sections give its lifetime and the way to revoke access.

## Token lifetime

A token is valid until it expires. If you disable a user or change a role in the identity provider, nothing changes for a token that the user already has. It keeps its rights until it expires. The next token has the new role.

The cache key includes the role, so a token with another role never reads an answer that the cache stored for the old role.

Set the access token lifetime to **5 minutes**. This is the default of Keycloak, and the `k3d` realm sets it in `accessTokenLifespan`. In Keycloak, open *Realm settings*, *Tokens*, and set *Access Token Lifespan*. A client can override it under *Clients*, *Advanced*, *Advanced settings*. Other providers have the same setting under another name.

## Revoke access

Delete the grant:

```bash
curl --request DELETE \
  --header "Authorization: Bearer ${POLICY_WRITER_TOKEN}" \
  --header "Accept-Profile: test" \
  "${BASE_URL}/access_policy?subject=eq.test_user_1&unit_type=eq.region&unit_id=eq.region_1"
```

The database removes the row from `test.access_policy` and writes its previous state to `test.access_log` with `action = 'delete'`. To grant access again, insert the row again. You need no resync or token refresh. Every change of an access policy row makes the cached answers outdated, whether it passed the proxy or not. A trigger on the revision counter queues a reload of the proxy ([Proxy](../proxy.md#cache)).

<!-- nav:start -->
---

[Previous: Identity provider](identity.md) | [Next: Troubleshooting](troubleshooting.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
