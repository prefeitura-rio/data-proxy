# The first read is a miss and stores the answer. The second read is a hit, keeps the source and the
# snapshot, and does not call PostgREST.
GET http://{{host}}/people?case=hit

HTTP 200
X-Cache: MISS
X-DuckLake-Snapshot: 7
[Asserts]
jsonpath "$.n" == 1

GET http://{{host}}/people?case=hit

HTTP 200
X-Cache: HIT
X-Source: ducklake
X-DuckLake-Snapshot: 7
[Asserts]
jsonpath "$.n" == 1

# Each identity has its own cache entry, so one user never gets the cached answer of another.
# The identity is the subject and the schemas claim. A request without a token is anonymous.
GET http://{{host}}/people?case=identity
Authorization: Bearer h.eyJzdWIiOiJhbGljZSJ9.s

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 1

GET http://{{host}}/people?case=identity
Authorization: Bearer h.eyJzdWIiOiJhbGljZSJ9.s

HTTP 200
X-Cache: HIT
[Asserts]
jsonpath "$.n" == 1

GET http://{{host}}/people?case=identity
Authorization: Bearer h.eyJzdWIiOiJib2IifQ.s

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 2

GET http://{{host}}/people?case=identity
Authorization: Bearer h.eyJzdWIiOiJhbGljZSJ9.s

HTTP 200
X-Cache: HIT
[Asserts]
jsonpath "$.n" == 1

GET http://{{host}}/people?case=identity
Authorization: Bearer h.eyJzdWIiOiJib2IifQ.s

HTTP 200
X-Cache: HIT
[Asserts]
jsonpath "$.n" == 2

GET http://{{host}}/people?case=identity
Authorization: Bearer h.eyJzdWIiOiJhbGljZSIsInNjaGVtYXMiOlsicGljIl19.s

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 3

GET http://{{host}}/people?case=identity
Authorization: Bearer h.eyJzdWIiOiJhbGljZSIsInNjaGVtYXMiOlsicGljIl19.s

HTTP 200
X-Cache: HIT
[Asserts]
jsonpath "$.n" == 3

GET http://{{host}}/people?case=identity

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 4

GET http://{{host}}/people?case=identity

HTTP 200
X-Cache: HIT
[Asserts]
jsonpath "$.n" == 4

# The paths in noCachePaths are never cached, and neither are their subpaths. Every read calls PostgREST.
GET http://{{host}}/access_policy

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 1

GET http://{{host}}/access_policy

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 2

GET http://{{host}}/access_policy/1

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 1

GET http://{{host}}/access_policy/1

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 2

GET http://{{host}}/private

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 1

GET http://{{host}}/private

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 2

# A 404 is not stored, so the second read calls PostgREST again.
GET http://{{host}}/gone

HTTP 404
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 1

GET http://{{host}}/gone

HTTP 404
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 2

# An empty answer is never stored. It can mean that access was denied or that data is not published yet.
GET http://{{host}}/empty

HTTP 200
X-Cache: MISS
[Asserts]
body == "[]"

GET http://{{host}}/empty

HTTP 200
X-Cache: MISS
[Asserts]
body == "[]"

# A ranged read is never stored, so the second read calls PostgREST again.
GET http://{{host}}/people?case=range
Range: items=0-1

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 1

GET http://{{host}}/people?case=range
Range: items=0-1

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 2

# A response that is not JSON is never stored.
GET http://{{host}}/text

HTTP 200
X-Cache: MISS
[Asserts]
body == "hello"

GET http://{{host}}/text

HTTP 200
X-Cache: MISS
[Asserts]
body == "hello"

# A body above the size limit (262144 bytes in this config) is never stored.
GET http://{{host}}/big

HTTP 200
X-Cache: MISS
[Asserts]
bytes count > 262144

GET http://{{host}}/big

HTTP 200
X-Cache: MISS
[Asserts]
bytes count > 262144

# A HEAD request is never cached, and it does not leave an empty entry for a later GET.
HEAD http://{{host}}/people?case=head

HTTP 200
[Asserts]
header "X-Cache" == "MISS"

HEAD http://{{host}}/people?case=head

HTTP 200
[Asserts]
header "X-Cache" == "MISS"

GET http://{{host}}/people?case=head

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.upstream" == "postgrest"

GET http://{{host}}/people?case=head

HTTP 200
X-Cache: HIT
[Asserts]
jsonpath "$.upstream" == "postgrest"

# A successful write to the access policy makes every cached answer stale, so nobody reads an answer
# from before the change. A repeat of the read after that is cached again.
GET http://{{host}}/people?case=policy

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 1

GET http://{{host}}/people?case=policy

HTTP 200
X-Cache: HIT
[Asserts]
jsonpath "$.n" == 1

POST http://{{host}}/access_policy
Content-Type: application/json
{"subject":"alice","unit_type":"unit","unit_id":"unit_1"}

HTTP 200

GET http://{{host}}/people?case=policy

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 2

GET http://{{host}}/people?case=policy

HTTP 200
X-Cache: HIT
[Asserts]
jsonpath "$.n" == 2
