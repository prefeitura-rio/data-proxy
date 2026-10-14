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

# Redis is down, so the proxy answers from PostgREST and stores nothing. Every read is a miss.
GET http://{{host_redis_down}}/people?case=redis-down

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 1

GET http://{{host_redis_down}}/people?case=redis-down

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 2

# The proxy limits the requests that wait for PostgREST. This proxy allows one, so a second request that
# starts while the first one waits gets a quick 503 with a retry delay.
GET http://{{host_limited}}/nested

HTTP 200
[Asserts]
jsonpath "$.inner_status" == 503
jsonpath "$.inner_retry_after" == "1"

# The slot is free again afterwards.
GET http://{{host_limited}}/people?case=after-limit

HTTP 200
[Asserts]
jsonpath "$.upstream" == "read"
