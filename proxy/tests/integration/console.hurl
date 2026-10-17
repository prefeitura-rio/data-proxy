# The console profile belongs to the cluster. The proxy refuses it, so that the config endpoint that the
# proxy itself calls is not reachable from outside.
GET http://{{host}}/rpc/config
Accept-Profile: console

HTTP 403
[Asserts]
jsonpath "$.error" contains "console schema"

POST http://{{host}}/schemas
Content-Profile: console
Content-Type: application/json
```
{"name": "alpha"}
```

HTTP 403

# Other profiles pass.
GET http://{{host}}/people
Accept-Profile: test

HTTP 200
