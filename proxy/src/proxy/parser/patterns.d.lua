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

--- What the proxy sends to PostgreSQL in the X-DuckLake-Filter header.
---@class FilterDocument
---@field where FilterNode|nil The filter.
---@field columns string[]|nil The columns that the request reads. Absent means every column.
---@field order FilterOrder[]|nil The order of the rows. Only sent with a limit.
---@field limit integer|nil The number of rows that PostgREST can return at most, with the offset added.

--- The schema of the filter that the proxy sends to PostgreSQL.
---@class SchemasModule
---@field filter_document Schema<FilterDocument>
