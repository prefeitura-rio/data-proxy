# Query pushdown

The proxy forwards what it reads from a query to PostgreSQL, in the `X-DuckLake-Filter` header. The table view can then read less data from the sources.

| Part | What the table view does with it |
| --- | --- |
| `where` | Filters the rows before it reads the sources. |
| `columns` | Reads only the selected columns, the order columns, and the filter columns. The other columns come back as null. |
| `limit` and `order` | Stops after the first rows. The limit includes the `offset`. |

PostgREST still applies `select`, `order`, `limit`, `offset`, embeds, and content negotiation to the rows that the table view returns. A pushed part only makes that set smaller, so the answer stays the same.

The proxy sends `columns` and `limit` only when it reads every parameter of the query. A parameter that it can't read, such as a filter that its grammar doesn't accept, stops both. A `select` with an alias, a cast, an embed, or an aggregate stops them too. The proxy also drops `limit` for a request that needs every row:

- A request with `Prefer: count=...` or a `Range` header.
- A request for a single object or another media type of PostgREST.
- A `HEAD` request.

The table view pushes `order` with the limit only when every order column is a whole number, a date, or a true or false value. DuckDB sorts text by bytes, and PostgreSQL sorts it by the collation, so a limit on a text order could return other rows. Columns of other types reach PostgreSQL as text, and the same rule applies to them.

A request that selects all columns of a wide table still reads all of them. A `count` of a table or a request without `limit` reads every row too.

The parser reads the query parameters that contain comparisons and the `and`, `or` and `not` groups. It decodes the escapes in their keys and values. The grammar is LPeg, in `proxy/src/proxy/parser/patterns.lua`. The proxy checks the document that the parser builds with the `filter_document` schema before it sends it. A document that the PostgreSQL side can't render therefore fails in the proxy, with the field named.

<!-- nav:start -->
---

[Previous: Proxy](index.md) | [Next: Cache](cache.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
