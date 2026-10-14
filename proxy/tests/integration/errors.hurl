# A read that PostgREST fails once is sent again, and the client gets the second answer.
GET http://{{host}}/flaky

HTTP 200
[Asserts]
jsonpath "$.n" == 2

# PostgREST answers 503 every time. The proxy sends the read three times, then returns the last answer.
GET http://{{host}}/always-down

HTTP 503
[Asserts]
jsonpath "$.upstream" == "postgrest"
jsonpath "$.n" == 3

# A write is sent once, because a second write could change the data twice.
POST http://{{host}}/always-down
Content-Type: application/json
{"a":1}

HTTP 503
[Asserts]
jsonpath "$.n" == 1

# PostgREST closes the connection, so the proxy answers with its own 502.
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
jsonpath "$.upstream" == "postgrest"
