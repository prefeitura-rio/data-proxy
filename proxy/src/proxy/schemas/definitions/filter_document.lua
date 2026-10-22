-- The fields of the filter_document schema: the filter that the proxy sends to PostgreSQL.

local constants = require("proxy.constants")
local regex = require("proxy.regex")
local types = require("tableshape").types

--- A column name, as the grammar accepts it.
local column = types.pattern(regex.column_name)

--- The operators that take one scalar value, from constants.filter.scalar_operators.
local scalar_operators = types.one_of(constants.filter.scalar_operators)

--- The keywords that the is operator accepts, from constants.filter.is_keywords.
local is_keywords = types.one_of(constants.filter.is_keywords)

--- One item or more. The grammar never makes an empty group or an empty in list.
local at_least_one = types.range(1, math.huge)

--- Make the shape of a comparison: one column, one operator, one value.
---@param operator string|tableshape.Type<any>
---@param value tableshape.Type<any>
---@return tableshape.Type<FilterComparison>
local function comparison(operator, value)
	return types.shape({
		kind = "comparison",
		column = column,
		operator = operator,
		negated = types.boolean,
		value = value,
	})
end

--- A comparison or a group. A group holds nodes, so the shape refers to itself.
---@type tableshape.Type<FilterNode>
local node

--- The shape of a group: and(id.eq.1,name.eq.Rio), or not.or(...).
local group = types.shape({
	kind = "group",
	operator = types.one_of({ "and", "or" }),
	items = types.array_of(types.proxy(function() return node end), { length = at_least_one }),
	negated = types.boolean:is_optional(),
})

node = types
	.one_of({
		comparison(scalar_operators, types.string),
		comparison("in", types.array_of(types.string, { length = at_least_one })),
		comparison("is", is_keywords),
		group,
	})
	:describe("a comparison or a group")

return {
	where = node,
}
