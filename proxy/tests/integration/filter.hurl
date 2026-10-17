# The parsed where document crosses to PostgREST in the X-DuckLake-Filter header.
GET http://{{host}}/echo?id=eq.42

HTTP 200
[Asserts]
jsonpath "$.where.kind" == "comparison"
jsonpath "$.where.column" == "id"
jsonpath "$.where.operator" == "eq"
jsonpath "$.where.negated" == false
jsonpath "$.where.value" == "42"

# The proxy builds X-DuckLake-Filter itself. A filter header that the client sends never reaches
# PostgREST, so a client cannot choose its own filter.
GET http://{{host}}/echo?case=forged-none
X-DuckLake-Filter: {"where":{"kind":"comparison","column":"secret","operator":"eq","negated":false,"value":"1"}}

HTTP 200
[Asserts]
body == "none"

GET http://{{host}}/echo?id=eq.43
X-DuckLake-Filter: {"where":{"kind":"comparison","column":"secret","operator":"eq","negated":false,"value":"1"}}

HTTP 200
[Asserts]
jsonpath "$.where.column" == "id"
jsonpath "$.where.value" == "43"
