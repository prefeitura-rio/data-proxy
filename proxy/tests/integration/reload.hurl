# This proxy reads its settings from the console, and the console tells it when they change.
# The first sync.json lists /private in noCachePaths, so the proxy never caches it.
GET http://{{host_reload}}/private

HTTP 200
X-Cache: MISS

GET http://{{host_reload}}/private

HTTP 200
X-Cache: MISS

# The console now serves a sync.json without /private, with a new revision. The proxy does not know yet.
POST http://{{console_reload}}/console/set?etag=%222%22
```
{"noCachePaths":["/access_policy"]}
```

HTTP 200

GET http://{{host_reload}}/private

HTTP 200
X-Cache: MISS

GET http://{{host_reload}}/private

HTTP 200
X-Cache: MISS

# The reload takes the new settings. The proxy then caches /private.
POST http://{{admin_reload}}/reload

HTTP 200
[Asserts]
jsonpath "$.changed" == true
jsonpath "$.revision" == "\"2\""

GET http://{{host_reload}}/private?case=after-reload

HTTP 200
X-Cache: MISS

GET http://{{host_reload}}/private?case=after-reload

HTTP 200
X-Cache: HIT

# A second reload with the same revision changes nothing.
POST http://{{admin_reload}}/reload

HTTP 200
[Asserts]
jsonpath "$.changed" == false

# A console that fails does not change the settings of the proxy. The reload answers 502.
POST http://{{console_reload}}/console/set?etag=%223%22&status=503
```
{"noCachePaths":[]}
```

HTTP 200

POST http://{{admin_reload}}/reload

HTTP 502
[Asserts]
jsonpath "$.error" == "Could not reload sync.json"

GET http://{{host_reload}}/access_policy?case=after-failure

HTTP 200
X-Cache: MISS

GET http://{{host_reload}}/access_policy?case=after-failure

HTTP 200
X-Cache: MISS

# A document that does not match is refused the same way.
POST http://{{console_reload}}/console/set?etag=%224%22
```
{"noCachePaths":[1]}
```

HTTP 200

POST http://{{admin_reload}}/reload

HTTP 502
[Asserts]
jsonpath "$.error" == "Could not reload sync.json"

# The reload answers only to POST.
GET http://{{admin_reload}}/reload

HTTP 405

# A proxy without a console address has nothing to reload.
POST http://{{admin}}/reload

HTTP 409
[Asserts]
jsonpath "$.error" contains "PROXY_CONFIG_URL"
