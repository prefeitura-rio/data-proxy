-- The LPeg grammar that parses one filter parameter into a filter node.
--
-- ```lua
-- patterns.filter_grammar:match("id.eq.42")  --> { kind = "comparison", ... }
-- patterns.filter_grammar:match("select")     --> nil
-- ```

local constants = require("proxy.constants")
local lpeg = require("lpeg")
local tablex = require("pl.tablex") --[[@as pl.tablex]]

local P, R, S, V = lpeg.P, lpeg.R, lpeg.S, lpeg.V
local C, Cc, Cs, Ct = lpeg.C, lpeg.Cc, lpeg.Cs, lpeg.Ct

--- The LPeg patterns that parse the filter grammar, in dependency order.
local patterns = {}

--- Make a pattern that matches one of the words, in the order of the list.
--- LPeg commits to the first match, so a shorter word must not come before a longer one.
---
--- ```lua
--- C(match_one_of({ "gt", "gte" })):match("gte") --> "gt"
--- C(match_one_of({ "gte", "gt" })):match("gte") --> "gte"
--- ```
---@param words string[]
---@return lpeg.Pattern
local function match_one_of(words)
	return tablex.reduce(function(acc, word) return acc + P(word) end, words, P(false))
end

--- Make a comparison node. LPeg calls this function with the captures of a comparison.
---@param negated boolean true if not. is before the column (not.id.eq.1).
---@param column string Column name.
---@param operator_negated boolean true if not. is before the operator (id.not.eq.1).
---@param operator string Operator name, for example eq or in.
---@param value string|string[] Scalar text, or the items of an in list.
---@return FilterComparison
local function make_comparison_node(negated, column, operator_negated, operator, value)
	return {
		kind = "comparison",
		column = column,
		operator = operator,
		negated = negated or operator_negated,
		value = value,
	}
end

--- Make a group node for and or or. LPeg calls this function with the captures of a group.
---
--- ```lua
--- make_group_node(true, "and", { <comparison>, <comparison> })
--- --> { kind = "group", operator = "and", negated = true, items = { ... } }
--- ```
---@param negated boolean true for not.and(...) and not.or(...).
---@param operator string The operator: "and" or "or".
---@param items FilterNode[] The members of the group.
---@return FilterGroup
local function make_group_node(negated, operator, items)
	local node = { kind = "group", operator = operator, items = items }
	if negated then node.negated = true end
	return node
end

--- Make the rule for one comparison. The caller selects how a scalar value ends.
---
--- ```lua
--- make_comparison_rule(patterns.top_scalar):match("id.eq.42")
--- --> { kind = "comparison", column = "id", operator = "eq", negated = false, value = "42" }
--- ```
---@param scalar lpeg.Pattern Pattern for the value of the scalar operators.
---@return lpeg.Pattern rule Captures one FilterComparison.
local function make_comparison_rule(scalar)
	local operation = patterns.scalar_operator * "." * scalar
		+ C("in") * "." * patterns.list
		+ C("is") * "." * patterns.is_keyword
	return (patterns.negation * patterns.column_name * "." * patterns.negation * operation) / make_comparison_node
end

--- A column name, for example id or name_2. It does not match 2name or a-b.
--- It captures the name.
---
--- ```lua
--- patterns.column_name:match("id")    --> "id"
--- patterns.column_name:match("2name") --> nil
--- ```
---@type lpeg.Pattern
patterns.column_name = C((R("AZ", "az") + "_") * (R("AZ", "az", "09") + "_") ^ 0)

--- The optional prefix not. It captures true if the prefix is present and false if it is not.
--- If the prefix is present, the pattern consumes it.
---
--- ```lua
--- patterns.negation:match("not.id") --> true
--- patterns.negation:match("id")     --> false
--- ```
---@type lpeg.Pattern
patterns.negation = P("not.") * Cc(true) + Cc(false)

--- The double quote character. It starts and ends a quoted value.
---
--- ```lua
--- patterns.quote:match('"a"') --> 2
--- patterns.quote:match("a")   --> nil
--- ```
---@type lpeg.Pattern
patterns.quote = P('"')

--- One character in a quoted value. It is \x (the result is x),
--- or any character that is not a quote or a backslash.
---
--- ```lua
--- patterns.quoted_char:match("a") --> 2
--- patterns.quoted_char:match('"') --> nil
--- ```
---@type lpeg.Pattern
patterns.quoted_char = P("\\") / "" * P(1) + (1 - S('"\\'))

--- A quoted value. It can be empty, for example "a b" or "".
--- It captures the text without the quotes and without the escape backslashes.
--- This pattern is for the items of an in list.
---
--- ```lua
--- patterns.quoted:match('"a b"') --> "a b"
--- patterns.quoted:match('""')    --> ""
--- ```
---@type lpeg.Pattern
patterns.quoted = patterns.quote * Cs(patterns.quoted_char ^ 0) * patterns.quote

--- A quoted value with one or more characters, for example "a b". It does not match "".
--- This pattern is for scalar values, because PostgreSQL does not accept an empty scalar value.
---
--- ```lua
--- patterns.quoted_text:match('"a b"') --> "a b"
--- patterns.quoted_text:match('""')    --> nil
--- ```
---@type lpeg.Pattern
patterns.quoted_text = patterns.quote * Cs(patterns.quoted_char ^ 1) * patterns.quote

--- One item of an in list. The item is a quoted value,
--- or text up to the next comma, parenthesis, or quote.
--- It captures the text.
---
--- ```lua
--- patterns.list_item:match("open")      --> "open"
--- patterns.list_item:match('"on hold"') --> "on hold"
--- ```
---@type lpeg.Pattern
patterns.list_item = patterns.quoted + C((1 - S(',()"')) ^ 1)

--- The value of an in operator, for example (1,2,3) or (a,"b c").
--- It captures a table of the items.
--- The list must have one item or more. The text () does not match.
---
--- ```lua
--- patterns.list:match("(1,2,3)") --> { "1", "2", "3" }
--- ```
---@type lpeg.Pattern
patterns.list = P("(") * Ct(patterns.list_item * (P(",") * patterns.list_item) ^ 0) * P(")")

--- The keywords that the is operator accepts, from constants.filter.is_keywords.
--- It captures the keyword.
---
--- ```lua
--- patterns.is_keyword:match("null") --> "null"
--- patterns.is_keyword:match("nope") --> nil
--- ```
---@type lpeg.Pattern
patterns.is_keyword = C(match_one_of(constants.filter.is_keywords))

--- The operators that take one scalar value, from constants.filter.scalar_operators.
--- It captures the operator name. The list is longest name first, or gte matches as gt with an extra e.
---
--- ```lua
--- patterns.scalar_operator:match("gte") --> "gte"
--- ```
---@type lpeg.Pattern
patterns.scalar_operator = C(match_one_of(constants.filter.scalar_operators))

--- The scalar of a single comparison, for example name=eq.Rio.
--- It continues to the end of the text. It can contain commas and parentheses.
--- It cannot start with (. It cannot contain a quote, unless the whole value is one quoted string.
---
--- ```lua
--- patterns.top_scalar:match("Rio")   --> "Rio"
--- patterns.top_scalar:match('"Rio"') --> "Rio"
--- ```
---@type lpeg.Pattern
patterns.top_scalar = patterns.quoted_text * -1 + -P("(") * C((1 - patterns.quote) ^ 1) * -1

--- The scalar of a comparison in a group, for example or=(name.eq.Rio,id.eq.1).
--- It stops at a comma or a closing parenthesis, because these belong to the group.
--- It cannot start with (.
---
--- ```lua
--- patterns.nested_scalar:match("Rio") --> "Rio"
--- ```
---@type lpeg.Pattern
patterns.nested_scalar = patterns.quoted_text + -P("(") * C((1 - S(',()"')) ^ 1)

--- A text that is only a column name. The proxy uses it to check the key of a parameter.
---
--- ```lua
--- patterns.whole_column_name:match("id")   --> "id"
--- patterns.whole_column_name:match("id.x") --> nil
--- ```
---@type lpeg.Pattern
patterns.whole_column_name = patterns.column_name * -1

--- The complete grammar. It matches the whole text or nothing. For unknown text, it returns nil.
--- root: a group or a single comparison, with no text after it.
--- group: an optional not., then and or or, then one or more items in parentheses.
--- item: a group or a comparison inside a group.
--- This pattern comes after make_comparison_rule, because the grammar calls it.
---
--- ```lua
--- patterns.filter_grammar:match("id.eq.42")                         --> <comparison>
--- patterns.filter_grammar:match("id.not.eq.42")                     --> <comparison>
--- patterns.filter_grammar:match("not.or(id.eq.1,done.is.not_null)") --> <group>
--- patterns.filter_grammar:match('id.in.(1,"a,b")')                  --> <comparison>
--- ```
---@type lpeg.Pattern
patterns.filter_grammar = P({
	"root",
	root = (V("group") + make_comparison_rule(patterns.top_scalar)) * -1,
	group = (patterns.negation * C(P("and") + "or") * "(" * Ct(V("item") * (P(",") * V("item")) ^ 0) * ")")
		/ make_group_node,
	item = V("group") + make_comparison_rule(patterns.nested_scalar),
})

return patterns
