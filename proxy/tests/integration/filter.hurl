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

# The columns, the order and the limit cross to PostgREST too, when the proxy reads the whole query.
GET http://{{host}}/echo?select=id,name&id=eq.1&order=id.desc&limit=5&offset=2

HTTP 200
[Asserts]
jsonpath "$.where.column" == "id"
jsonpath "$.columns" count == 2
jsonpath "$.columns[0]" == "id"
jsonpath "$.columns[1]" == "name"
jsonpath "$.limit" == 7
jsonpath "$.order[0].column" == "id"
jsonpath "$.order[0].descending" == true

# A count needs every row, so the proxy keeps the columns and drops the limit.
GET http://{{host}}/echo?select=id&limit=5
Prefer: count=exact

HTTP 200
[Asserts]
jsonpath "$.columns[0]" == "id"
jsonpath "$.limit" not exists

# A parameter that the proxy does not read stops the push of the columns and the limit.
GET http://{{host}}/echo?select=id&limit=5&name=fts.x

HTTP 200
[Asserts]
body == "none"
