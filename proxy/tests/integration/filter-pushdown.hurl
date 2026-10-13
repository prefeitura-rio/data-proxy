# The parsed where document crosses to PostgREST in the X-DuckLake-Filter header.
GET http://{{host}}/echo?id=eq.42

HTTP 200
[Asserts]
jsonpath "$.where.kind" == "comparison"
jsonpath "$.where.column" == "id"
jsonpath "$.where.operator" == "eq"
jsonpath "$.where.negated" == false
jsonpath "$.where.value" == "42"
