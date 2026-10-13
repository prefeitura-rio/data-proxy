# HA: a write goes to the write endpoint and is never read from the cache.
POST http://{{host}}/people
Content-Type: application/json
{"a":1}

HTTP 200
[Asserts]
header "X-Cache" == "MISS"
jsonpath "$.upstream" == "write"
jsonpath "$.n" == 1
