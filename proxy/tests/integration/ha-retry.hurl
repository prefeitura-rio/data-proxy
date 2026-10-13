# HA: the read endpoint fails with 503, so the proxy retries on the write endpoint.
GET http://{{host}}/down

HTTP 200
[Asserts]
jsonpath "$.upstream" == "write"
jsonpath "$.n" == 1
