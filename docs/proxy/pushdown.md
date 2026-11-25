# Query pushdown

The proxy forwards what it reads from a query to PostgreSQL, in the `X-DuckLake-Filter` header. The table view can then read less data from the sources. The parser reads comparisons and the `and`, `or` and `not` groups, and it decodes the escapes. The grammar is LPeg, in `proxy/src/proxy/parser/patterns.lua`.

| Part                | What the table view does with it                                                                                 |
| ------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `where`             | Filters the rows before it reads the sources.                                                                    |
| `columns`           | Reads only the selected columns, the order columns, and the filter columns. The other columns come back as null. |
| `limit` and `order` | Stops after the first rows. The limit includes the `offset`.                                                     |

PostgREST still applies `select`, `order`, `limit`, `offset`, embeds and content negotiation to the rows that the table view returns. A pushed part only makes that set smaller, so the answer stays the same.

The proxy sends `columns` and `limit` only when it reads every parameter of the query. A parameter that it can't read, such as a filter that its grammar doesn't accept, stops both. A `select` with an alias, a cast or an embed stops them too. A `select` with aggregates sends `columns` and never `limit`, as the next section shows. The proxy also drops `limit` for a request that needs every row:

- A request with `Prefer: count=...` or a `Range` header. The [counts](#counts) section shows the exception.
- A request for a single object or another media type of PostgREST.
- A `HEAD` request.

The table view pushes `order` with the limit only when every order column is a whole number, a date or a true or false value. DuckDB sorts text by bytes, and PostgreSQL sorts it by the collation, so a limit on a text order could return other rows. A timestamp reaches PostgreSQL as text, because PostgreSQL keeps microseconds and DuckDB keeps nanoseconds. The same rule applies to it and to the other columns that reach PostgreSQL as text.

A request that selects all columns of a wide table still reads all of them.

## Column types

The table view gives each column the type that a native PostgreSQL table gives it, so PostgREST filters, orders, sums and groups it as it does on that table.

| DuckDB type                                         | PostgreSQL type    | What a client reads                                       |
| --------------------------------------------------- | ------------------ | --------------------------------------------------------- |
| `INTEGER`, `BIGINT`                                 | `bigint`           | A number.                                                 |
| `DECIMAL(p,s)`                                      | `numeric`          | A number, as in `9999000.00`. It was a quoted text before. |
| `FLOAT`, `DOUBLE`                                   | `double precision` | A number. It was a quoted text before.                    |
| `DATE`, `BOOLEAN`                                   | `date`, `boolean`  | The same value.                                           |
| `JSON`, `STRUCT`, `MAP`, `UNION`, list and array    | `jsonb`            | An object or an array. A JSON column is not a quoted text. |
| `TIMESTAMP` and every other type                    | `text`             | A text.                                                   |

A client that read a decimal or a double as a quoted text now reads a number. A client that parsed a JSON column from a string now reads the object, and it can use a JSON path in the query, such as `select=payload->>k` or `select=n:payload->n::int.sum()`.

The table function returns a nested column as JSON text, and the table view converts it to `jsonb`. `pg_duckdb` pushes a cast to `jsonb` into DuckDB, which has no such type, so the cast can't sit in the function. DuckDB prints a struct as `{'a': 1}` and a list as `[t1, x]`, which PostgreSQL does not accept as JSON, so the function asks DuckDB for `to_json(column)`.

An aggregate over a JSON path, such as `select=n:payload->n::int.sum()`, runs in PostgreSQL, over the rows that the table view returns. The aggregate function declines every JSON column, so the proxy sends those requests to PostgREST.

```mermaid
{{#include ../diagrams/pushdown-decision.mmd}}
```

## Aggregates

A `select` with aggregates, for example `select=grp,numerador:qty.sum(),total:count()`, runs in DuckDB.

1. The proxy puts the group and the aggregates in the `aggregate` field of `X-DuckLake-Filter`.
2. It calls `GET /rpc/agg?table=<table>` with the same headers. The function `agg` of the schema serves every table.
3. The function applies the row policy and the filter, runs the `GROUP BY` in DuckDB and returns the result rows as JSON.
4. PostgreSQL orders and cuts the rows of the groups. DuckDB cuts them first when the order is safe, as the next table shows.

| Part of the select                                       | Supported                                                                                                                                          |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Plain columns, with or without an alias (`alias:column`) | They form the group. Text, whole-number, date and true-or-false columns only.                                                                      |
| `sum()` of a column                                      | A whole-number column or a decimal of 18 digits or fewer. The result equals the sum of PostgreSQL.                                                 |
| `avg()` of a column                                      | The same columns. DuckDB gives the sum and the count, and PostgreSQL divides them, so the result has every digit of the `avg()` of PostgreSQL.     |
| `min()` and `max()` of a column                          | A whole number, a decimal, a double, a date or a timestamp without a time zone. A timestamp comes out as `2026-01-01T00:00:10`, as the table gives. |
| `count()` and `column.count()`                           | Any column that doesn't contain JSON.                                                                                                              |
| `order`                                                  | Columns of the group, by the name of the column, with `asc`, `desc`, `nullsfirst` and `nullslast`.                                                  |
| `limit` and `offset`                                     | Yes. They apply to the rows of the groups, after the order.                                                                                        |

A double has no `sum()` or `avg()` in the function. DuckDB and PostgreSQL add the same numbers in another order and give other last digits, and the digits change with the version of DuckDB. PostgreSQL sums it from the table.

DuckDB orders and cuts the groups itself when the `order` names every group column and each one is a whole number, a date or a true-or-false value. DuckDB then keeps the first `offset + limit + 1` groups of the order, and PostgreSQL cuts the page from them, so a table with millions of groups sends only a few to PostgreSQL. DuckDB gets the same null order as PostgreSQL: nulls last in an ascending order and first in a descending one. Text sorts by bytes in DuckDB and by the collation in PostgreSQL, so a text order stays in PostgreSQL. An order that leaves out a group column stays in PostgreSQL, because two groups could tie and the two engines could cut different ones.

The proxy sends the original request to PostgREST in these cases:

- The `order` names an aggregate, an alias or a column outside the group.
- The group has a decimal, floating point, timestamp or JSON column.
- The select has a function other than `sum`, `avg`, `count`, `min` and `max`, a cast, an embed, a star or two values with one name.
- The request has a `Range` header, asks for another media type than JSON, is a `HEAD` or isn't a read. A `Prefer: count=...` goes to the count that the next section describes.
- The function answers `null`, doesn't exist or fails. It answers `null` when a fallback source serves a part of the table. It also answers `null` for a type that the table above does not list, such as the `sum()` of a double, the `min()` of text or a column with a time zone.

The proxy writes `aggregate-declined` to the log when the function answers `null`, and `aggregate-failed` with the status when it fails. Count the lines of each event to see how often requests leave the fast path.

The proxy never sends `order` or `limit` in the filter of an aggregate.

The function gives at most `postgrest.maxRows` rows. With more rows it gives the first rows with the status `206` and `Content-Range: <first>-<last>/*`. A request with a `limit` below the cap gets exactly that many rows and no `206`. The proxy sends the `206` unchanged and doesn't store it.

A complete answer has no `Content-Range` header. It has the same `X-Source` and `X-DuckLake-Snapshot` headers as the table, and the cache stores it under the key of the original request.

## Counts

A `GET` of a table with `Prefer: count=exact`, `count=planned` or `count=estimated` and a `limit` counts in DuckDB. PostgreSQL has no statistics for a table behind a function, so the planned and the estimated count give a fixed guess of 1000 rows there. The proxy counts the real rows for all three and reports the mode that the client asked for in `Preference-Applied`. It calls `agg` with the table, the filter and `count()`. Then it reads the page with the `limit`, pinned to the snapshot of the count. It builds `Content-Range` and `Preference-Applied`, and sends `206` unless the page has every row. Other counts, `Range`, `HEAD`, an empty page and a table with a fallback source go to PostgREST. A planned or estimated count that goes there changes to `count=exact` in the `Prefer` header, because the plan of a table function always says 1000 rows.
