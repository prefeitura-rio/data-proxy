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
