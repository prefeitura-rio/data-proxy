# Twelve requests for the same key start together, while the cache is empty. One of them asks PostgREST.
# The other eleven wait for it and read its entry, so PostgREST gets one call and all twelve succeed.
# A request after them is a cache hit, because the leader stored the answer.
GET http://{{host}}/fan

HTTP 200
[Asserts]
jsonpath "$.ok" == 12
jsonpath "$.upstream_calls" == 1
jsonpath "$.after" == "HIT"
