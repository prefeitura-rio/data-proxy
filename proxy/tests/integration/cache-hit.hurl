# The first read is a miss and stores the answer. The second read is a hit and does not call PostgREST.
GET http://{{host}}/people?case=hit

HTTP 200
X-Cache: MISS
[Asserts]
jsonpath "$.n" == 1

GET http://{{host}}/people?case=hit

HTTP 200
X-Cache: HIT
X-Source: ducklake
[Asserts]
jsonpath "$.n" == 1
