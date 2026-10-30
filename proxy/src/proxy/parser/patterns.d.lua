---@meta

--- A comparison in a filter, for example id.eq.42.
---@class FilterComparison
---@field kind "comparison" Tells a comparison node from a group node.
---@field column string Column name, for example id.
---@field operator string One of eq, neq, gt, gte, lt, lte, like, ilike, in, is.
---@field negated boolean true if not. was written before the column or before the operator.
---@field value string|string[] Scalar text ("42"), or the items of an in list ({ "a", "b" }).

--- An and group or an or group of filter nodes.
---@class FilterGroup
---@field kind "group" Tells a group node from a comparison node.
---@field operator "and"|"or" How the members are combined.
---@field items FilterNode[] The members of the group, comparisons or nested groups.
---@field negated boolean|nil true for not.and(...) and not.or(...). Absent otherwise.

--- A comparison or a group.
---@alias FilterNode FilterComparison|FilterGroup

--- One column of the order that the proxy pushes.
---@class FilterOrder
---@field column string Column name.
---@field descending boolean true for desc.
---@field nulls "first"|"last"|nil Where the nulls go. Absent means the PostgreSQL default.

--- One aggregate of a select, for example numerador:participante_quantidade.sum().
---@class FilterAggregateItem
---@field fn "sum"|"avg"|"count"|"min"|"max" The aggregate function.
---@field column string|nil The column. Absent for count().
---@field alias string The name of the value in the answer.

--- One column of the group by, with the name that it has in the answer.
---@class FilterGroupColumn
---@field column string The column of the table.
---@field alias string The name of the value in the answer. It is the column if the select has no alias.

--- The group by and the aggregates that DuckDB can run for a request.
---@class FilterAggregate
---@field group FilterGroupColumn[]|nil The plain columns of the select. Absent for an aggregate without a group.
---@field items FilterAggregateItem[] The aggregates of the select.
---@field order FilterOrder[]|nil The order of the groups. Each entry names a column of the group.
---@field limit integer|nil The most rows of the answer.
---@field offset integer|nil The number of rows to skip before the first row of the answer.

--- What the proxy sends to PostgreSQL in the X-DuckLake-Filter header.
---@class FilterDocument
---@field where FilterNode|nil The filter.
---@field columns string[]|nil The columns that the request reads. Absent means every column.
---@field order FilterOrder[]|nil The order of the rows. Only sent with a limit.
---@field limit integer|nil The number of rows that PostgREST can return at most, with the offset added.
---@field aggregate FilterAggregate|nil The group by and the aggregates of a select, without an order or a limit.

--- The schema of the filter that the proxy sends to PostgreSQL.
---@class SchemasModule
---@field filter_document Schema<FilterDocument>
