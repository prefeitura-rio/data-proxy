# An aggregate read goes to the aggregate function of the table, with the aggregate in the filter header
# and no query string. The answer keeps the source and the snapshot.
GET http://{{host}}/agg_ok?select=grp,n:qty.sum()&unit=eq.u1

HTTP 200
X-Source: ducklake
X-DuckLake-Snapshot: 7
[Asserts]
jsonpath "$[0].via" == "rpc"
jsonpath "$[0].args" == ""
jsonpath "$[0].filter.aggregate.group[0].column" == "grp"
jsonpath "$[0].filter.aggregate.group[0].alias" == "grp"
jsonpath "$[0].filter.aggregate.items[0].fn" == "sum"
jsonpath "$[0].filter.aggregate.items[0].column" == "qty"
jsonpath "$[0].filter.aggregate.items[0].alias" == "n"
jsonpath "$[0].filter.where.column" == "unit"
jsonpath "$[0].filter.columns" count == 3

# The answer is stored under the key of the original request. The second read is a hit and does not
# call the function again.
GET http://{{host}}/agg_ok?select=grp,n:qty.sum()&unit=eq.u1

HTTP 200
X-Cache: HIT
[Asserts]
jsonpath "$[0].n" == 1

# A function that answers null sends the original request to the table.
GET http://{{host}}/agg_null?select=grp,n:qty.sum()

HTTP 200
[Asserts]
jsonpath "$.upstream" == "postgrest"

# A function that does not exist sends the original request to the table.
GET http://{{host}}/agg_gone?select=grp,n:qty.sum()

HTTP 200
[Asserts]
jsonpath "$.upstream" == "postgrest"

# A read that needs every row, or another media type, never calls the function.
GET http://{{host}}/agg_ok?select=grp,n:qty.sum()&case=count
Prefer: count=exact

HTTP 200
[Asserts]
jsonpath "$.upstream" == "postgrest"

# The order, the limit and the offset of a group column go to the function inside the aggregate.
# The aggregate leaves the top of the filter, so the table function never cuts the rows before the sum.
GET http://{{host}}/agg_ok?select=grp,n:qty.sum()&order=grp.desc.nullslast&limit=3&offset=1

HTTP 200
[Asserts]
jsonpath "$[0].via" == "rpc"
jsonpath "$[0].filter.aggregate.order[0].column" == "grp"
jsonpath "$[0].filter.aggregate.order[0].descending" == true
jsonpath "$[0].filter.aggregate.order[0].nulls" == "last"
jsonpath "$[0].filter.aggregate.limit" == 3
jsonpath "$[0].filter.aggregate.offset" == 1
jsonpath "$[0].filter.limit" not exists
jsonpath "$[0].filter.order" not exists

# An alias on a column of the group goes to the function with the column and the alias.
GET http://{{host}}/agg_ok?select=grupo:grp,n:qty.sum()

HTTP 200
[Asserts]
jsonpath "$[0].via" == "rpc"
jsonpath "$[0].filter.aggregate.group[0].column" == "grp"
jsonpath "$[0].filter.aggregate.group[0].alias" == "grupo"

# An order on a value that is not a column of the group goes to the table.
GET http://{{host}}/agg_ok?select=grp,n:qty.sum()&order=qty

HTTP 200
[Asserts]
jsonpath "$.upstream" == "postgrest"

GET http://{{host}}/agg_ok?select=grp,n:qty.sum()&case=csv
Accept: text/csv

HTTP 200
[Asserts]
jsonpath "$.upstream" == "postgrest"

# A write never calls the function.
POST http://{{host}}/agg_ok?select=grp,n:qty.sum()
Content-Type: application/json
{"a":1}

HTTP 200
[Asserts]
jsonpath "$.upstream" == "postgrest"
