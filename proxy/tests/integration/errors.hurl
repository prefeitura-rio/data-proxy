# HA: both endpoints fail, so the proxy returns the last answer.
GET http://{{host}}/always-down

HTTP 503
[Asserts]
jsonpath "$.upstream" == "write"
jsonpath "$.n" == 2

# PostgREST closes both connections, so the proxy answers with its own 502.
GET http://{{host}}/closed

HTTP 502
[Asserts]
jsonpath "$.error" == "PostgREST unavailable"
header "X-Cache" not exists
