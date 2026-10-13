# PostgREST closes both connections, so the proxy answers with its own 502.
GET http://{{host}}/closed

HTTP 502
[Asserts]
jsonpath "$.error" == "PostgREST unavailable"
header "X-Cache" not exists
