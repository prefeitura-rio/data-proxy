# HA: both endpoints fail, so the proxy returns the last answer.
GET http://{{host}}/always-down

HTTP 503
[Asserts]
jsonpath "$.upstream" == "write"
jsonpath "$.n" == 2
