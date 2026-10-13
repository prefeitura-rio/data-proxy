# A read miss crosses to PostgREST, forwards the allowlisted header, and stores the answer.
GET http://{{host}}/people?case=miss
Authorization: Bearer t

HTTP 200
X-Cache: MISS
X-Source: ducklake
[Asserts]
jsonpath "$.upstream" == "read"
jsonpath "$.n" == 1
jsonpath "$.auth" == "Bearer t"
