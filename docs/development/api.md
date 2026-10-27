# Access the API

Add this local host entry:

```text
127.0.0.1 data-proxy.local
```

```bash
kubectl -n istio-ingress port-forward svc/istio-ingressgateway 3111:80
token
```

`token` prints a table with the token, its type, lifetime, scope, client, and realm. The local mode adds a port-forward command and a request example that uses `$TOKEN`. In Nushell, run `token | get token` to get only the token. Without options it asks the Keycloak chart in the local cluster for a `user` token. Use `--issuer` to ask any Keycloak realm directly:

```bash
token --issuer https://sso.example.com/realms/prod --client-id my-client --client-secret "$SECRET"
token --issuer https://sso.example.com/realms/prod --client-id my-client --username alice --password "$PASSWORD"
```

The client secret can also come from `KEYCLOAK_CLIENT_SECRET`. `--username` and `--password` switch to the password grant. `--scope` adds scopes.

See [Using the API](../using.md).

<!-- nav:start -->
---

[Previous: Seed BigQuery data](seed.md) | [Next: k6 commands](k6/index.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
