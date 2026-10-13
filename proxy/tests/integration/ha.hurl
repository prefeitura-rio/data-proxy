# HA: the read endpoint fails with 503, so the proxy retries on the write endpoint.
GET http://{{host}}/down

HTTP 200
[Asserts]
jsonpath "$.upstream" == "write"
jsonpath "$.n" == 1

# HA: a write goes to the write endpoint and is never read from the cache, even when it repeats.
POST http://{{host}}/people
Content-Type: application/json
{"a":1}

HTTP 200
[Asserts]
header "X-Cache" == "MISS"
jsonpath "$.upstream" == "write"
jsonpath "$.n" == 1

POST http://{{host}}/people
Content-Type: application/json
{"a":1}

HTTP 200
[Asserts]
header "X-Cache" == "MISS"
jsonpath "$.upstream" == "write"
jsonpath "$.n" == 2
