# This proxy checks its sync.json file every second. Kubernetes replaces the file when the provision job
# writes a new configuration. The first sync.json lists /private in noCachePaths, so the proxy never caches it.
GET http://{{host_reload}}/private

HTTP 200
X-Cache: MISS

GET http://{{host_reload}}/private

HTTP 200
X-Cache: MISS

# The file now has a sync.json without /private. The proxy takes it at its next check, and then it caches
# /private. The request repeats until the proxy has the new settings.
POST http://{{sync_reload}}/sync/set
```
{"noCachePaths":["/access_policy"]}
```

HTTP 200

GET http://{{host_reload}}/private?case=after-reload
[Options]
retry: 20
retry-interval: 500ms

HTTP 200
[Asserts]
header "X-Cache" == "HIT"

# A document that does not match changes nothing. The proxy keeps the settings that it has, so it still
# caches /private and still skips /access_policy.
POST http://{{sync_reload}}/sync/set
```
{"noCachePaths":[1]}
```

HTTP 200

GET http://{{host_reload}}/private?case=after-invalid
[Options]
delay: 3s

HTTP 200
X-Cache: MISS

GET http://{{host_reload}}/private?case=after-invalid

HTTP 200
X-Cache: HIT

GET http://{{host_reload}}/access_policy?case=after-invalid

HTTP 200
X-Cache: MISS

GET http://{{host_reload}}/access_policy?case=after-invalid

HTTP 200
X-Cache: MISS
