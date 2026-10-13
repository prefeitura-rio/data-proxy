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

--- The filter that the proxy sends to PostgreSQL.
---@class FilterDocument
---@field where FilterNode The filter sent to PostgreSQL in the X-DuckLake-Filter header.

--- The schema of the filter that the proxy sends to PostgreSQL.
---@class SchemasModule
---@field filter_document Schema<FilterDocument>
