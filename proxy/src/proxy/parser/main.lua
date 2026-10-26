-- Parse the query of a PostgREST read into the document of the X-DuckLake-Filter header.
-- The document has the filter, the columns that the request reads, and the order and limit.
--
-- ```lua
-- parser.parse("id=eq.42")                       --> { where = { kind = "comparison", column = "id", ... } }
-- parser.parse("id=eq.42&name=fts.x")            --> { where = { kind = "comparison", column = "id", ... } }
-- parser.parse("select=id,name")                 --> { columns = { "id", "name" } }
-- parser.parse("order=id&limit=5", { limit = true }) --> { order = { ... }, limit = 5 }
-- ```

local List = require("pl.List") --[[@as pl.ListClass]]
local constants = require("proxy.constants")
local patterns = require("proxy.parser.patterns")
local regex = require("proxy.regex")
local schemas = require("proxy.schemas.main")
local stringx = require("pl.stringx") --[[@as pl.stringx]]
local tablex = require("pl.tablex") --[[@as pl.tablex]]
local utils = require("proxy.utils")

--- The result for a parameter that is not a filter. The code never changes it.
---@type ParameterResult
local SKIPPED = { readable = true }

--- The result for a parameter with a percent escape that is not valid. The code never changes it.
---@type ParameterResult
local UNREADABLE = { readable = false }

--- Functions that read a query string into a filter.
---@class Parser
local parser = {}

--- Make the result of one parameter: skipped if the grammar matched nothing, or the node.
---
--- ```lua
--- make_parameter_result(nil)          --> { readable = true }
--- make_parameter_result(<comparison>) --> { node = <comparison>, readable = true }
--- ```
---@param node FilterNode|nil
---@return ParameterResult
local function make_parameter_result(node)
	if node == nil then return SKIPPED end
	return { node = node, readable = true }
end

--- Split one key=value query parameter and percent-decode both parts.
---@param parameter string One part of the query string, for example id=eq.42.
---@return {key: string, value: string}|nil decoded nil if the parameter cannot be used.
---@return ParameterResult|nil failure The result to return when decoded is nil.
local function decode_parameter(parameter)
	local key, raw_value = parameter:match(regex.parameter)
	if not key or not raw_value then return nil, SKIPPED end

	local decoded_key = utils.percent_decode(key)
	local decoded_value = utils.percent_decode(raw_value)
	if not decoded_key or not decoded_value then return nil, UNREADABLE end

	return { key = decoded_key, value = decoded_value }
end

--- Parse one key=value query parameter and make its filter result.
---
--- ```lua
--- parse_parameter("id=eq.42")             --> { node = <comparison>, readable = true }
--- parse_parameter("or=(id.eq.1,id.eq.2)") --> { node = <group>, readable = true }
--- parse_parameter("select=id,name")       --> { readable = true }   (not a filter)
--- parse_parameter("id=eq.")               --> { readable = true }   (invalid value)
--- parse_parameter("id=eq.100%")           --> { readable = false }  (bad percent escape)
--- ```
---@param parameter string One part of the query string, for example id=eq.42.
---@return ParameterResult result
local function parse_parameter(parameter)
	local decoded, failure = decode_parameter(parameter)
	if not decoded then
		---@cast failure ParameterResult
		return failure
	end

	local key, value = decoded.key, decoded.value
	if constants.filter.reserved_keys[key] then return { readable = true, reserved = key, value = value } end
	if constants.filter.non_filter_keys[key] then return SKIPPED end

	if constants.filter.group_keys[key] then
		if value:sub(1, 1) ~= "(" then return SKIPPED end
		return make_parameter_result(patterns.filter_grammar:match(key .. value))
	end

	if not patterns.whole_column_name:match(key) then return SKIPPED end
	return make_parameter_result(patterns.filter_grammar:match(key .. constants.filter.part_separator .. value))
end

--- Join filter nodes into one filter document. One node is used as it is.
--- Several nodes are combined with and. The items are a plain table, not a List.
---
--- ```lua
--- make_filter_document({ <comparison> }) --> { where = <comparison> }
--- make_filter_document({ <a>, <b> })     --> { where = { kind = "group", operator = "and", items = { ... } } }
--- make_filter_document({})               --> nil
--- ```
---@param nodes pl.List<FilterNode>
---@return FilterDocument|nil document nil if there are no nodes.
local function make_filter_document(nodes)
	if #nodes == 0 then return nil end
	if #nodes == 1 then return { where = nodes[1] } end
	return { where = { kind = "group", operator = "and", items = tablex.icopy({}, nodes) } }
end

--- Tell whether a text is a plain column name.
---@param name string
---@return boolean
local function is_column_name(name) return name:match(regex.column_name) ~= nil end

--- Read the select value as a list of column names.
--- A star gives nil, because the request reads every column.
---
--- ```lua
--- read_select("id,name") --> { "id", "name" }, true
--- read_select("id,*")    --> nil, true
--- read_select("id::text") --> nil, false
--- ```
---@param value string The value of the select parameter.
---@return string[]|nil columns nil if the request reads every column or the value is not supported.
---@return boolean supported false if the value has an alias, a cast, an embed, an aggregate, or anything else.
local function read_select(value)
	local names = List(stringx.split(value, constants.filter.list_separator))
	local supported = tablex.find_if(names, function(name) return name ~= "*" and not is_column_name(name) end) == nil
	if not supported or names:contains("*") then return nil, supported end
	return names, true
end

--- Read one item of the order value: a column with modifiers, for example id.desc.nullslast.
---@param item string
---@return FilterOrder|nil entry nil if the column or a modifier is not supported.
local function read_order_item(item)
	local parts = stringx.split(item, constants.filter.part_separator)
	local entry = { column = parts[1], descending = false }
	if not is_column_name(entry.column) then return nil end

	for index = 2, #parts do
		local modifier = constants.filter.order_modifiers[parts[index]]
		if modifier == nil then return nil end
		tablex.update(entry, modifier)
	end
	return entry
end

--- Read the order value as a list of order entries.
---
--- ```lua
--- read_order("id.desc.nullslast,name")
--- --> { { column = "id", descending = true, nulls = "last" }, { column = "name", descending = false } }
--- read_order("a->b") --> nil
--- ```
---@param value string The value of the order parameter.
---@return FilterOrder[]|nil entries nil if any item is not a column with the known modifiers.
local function read_order(value)
	local entries = List()
	for _, item in ipairs(stringx.split(value, constants.filter.list_separator)) do
		local entry = read_order_item(item)
		if entry == nil then return nil end
		entries:append(entry)
	end
	return entries
end

--- Read a limit or an offset. It is a whole number with at most constants.filter.max_limit_digits digits.
---@param value string
---@return integer|nil number nil if the value is not a whole number or has too many digits.
local function read_count(value)
	if not value:match(regex.whole_number) or #value > constants.filter.max_limit_digits then return nil end
	return tonumber(value)
end

--- The reader of each reserved key. It gives the value and true, or nil and false if it cannot read the text.
--- A value of nil with true is valid: select=* reads every column.
---@type table<string, fun(value: string): any, boolean>
local readers = {
	select = read_select,
	order = function(value)
		local entries = read_order(value)
		return entries, entries ~= nil
	end,
	limit = function(value)
		local count = read_count(value)
		return count, count ~= nil
	end,
	offset = function(value)
		local count = read_count(value)
		return count, count ~= nil
	end,
}

--- Collect the columns of a filter node and its members, in the order of the query.
---@param node FilterNode
---@param columns pl.List<string> The list that the function adds to.
local function collect_filter_columns(node, columns)
	if node.kind == "comparison" then
		columns:append(node.column)
		return
	end
	for _, item in ipairs(node.items) do
		collect_filter_columns(item, columns)
	end
end

--- Join lists and drop the repeated names. The first place of a name stays.
---@param lists pl.List<string>[]
---@return string[] columns
local function unique_columns(lists)
	local seen, columns = {}, {}
	for _, list in ipairs(lists) do
		for _, name in ipairs(list) do
			if not seen[name] then
				seen[name] = true
				columns[#columns + 1] = name
			end
		end
	end
	return columns
end

--- Read the reserved parameters of a query. Return nil if a key appears twice or a value is not supported.
---@param results ParameterResult[]
---@return table<string, any>|nil read The value of each reserved key that the query has.
local function read_reserved(results)
	local read, seen = {}, {}
	for _, result in ipairs(results) do
		local key = result.reserved
		if key then
			if seen[key] then return nil end
			seen[key] = true

			local value, supported = readers[key](result.value)
			if not supported then return nil end
			read[key] = value
		end
	end
	return read
end

--- Make the columns that the request reads: the selected ones, the order columns, and the filter columns.
---@param read table<string, any> The reserved values.
---@param nodes pl.List<FilterNode>
---@return string[] columns
local function make_columns(read, nodes)
	local filter_columns = List()
	nodes:foreach(function(node) collect_filter_columns(node, filter_columns) end)
	local order_columns = List(read.order or {}):map(function(entry) return entry.column end)
	return unique_columns({ read.select, order_columns, filter_columns })
end

--- Tell whether the query has a parameter that is neither a filter nor a reserved key.
--- PostgREST applies such a parameter after the function returns its rows, so the proxy cannot push the rest.
---@param results ParameterResult[]
---@return boolean
local function has_unread_parameter(results)
	return tablex.find_if(results, function(result) return result.node == nil and result.reserved == nil end) ~= nil
end

--- Make the columns, the order, and the limit that the proxy pushes.
---@param results ParameterResult[]
---@param nodes pl.List<FilterNode>
---@param options ParseOptions
---@return table|nil plan nil if the proxy cannot push anything.
local function make_plan(results, nodes, options)
	if has_unread_parameter(results) then return nil end

	local read = read_reserved(results)
	if read == nil then return nil end

	local plan = { columns = read.select and make_columns(read, nodes) }
	if options.limit and read.limit then
		plan.limit = read.limit + (read.offset or 0)
		plan.order = read.order
	end
	return plan
end

--- Make the document from the results of the parameters: the filter and the plan.
---@param results pl.List<ParameterResult>
---@param options ParseOptions
---@return table document Empty if there is nothing to push.
local function make_document(results, options)
	local nodes = results
		:filter(function(result) return result.node ~= nil end)
		:map(function(result) return result.node end)

	return tablex.update(make_filter_document(nodes) or {}, make_plan(results, nodes, options) or {})
end

--- Parse the query string of a request into the document of the X-DuckLake-Filter header.
---
--- The filter pushes the comparisons and the and, or, and not groups that the grammar accepts.
--- The columns, the order, and the limit come only from a query that the proxy read completely.
--- With options.limit false the document has no limit and no order, because the caller knows that
--- PostgREST needs every row, for example to count them.
--- If a percent escape is not valid, the function returns nil. The proxy cannot read the request correctly.
---
--- ```lua
--- parser.parse("id=eq.42")
--- --> { where = { kind = "comparison", column = "id", operator = "eq", negated = false, value = "42" } }
--- parser.parse("id=eq.1&name=eq.Rio") --> { where = { kind = "group", operator = "and", items = { ... } } }
--- parser.parse("select=id&id=eq.1")   --> { where = { ... }, columns = { "id" } }
--- parser.parse("limit=5", { limit = true }) --> { limit = 5 }
--- parser.parse("select=*")            --> nil
--- ```
---@param args string|nil The raw query string, for example id=eq.1&name=like.*rio*.
---@param options ParseOptions|nil limit: true if the caller allows the limit and the order.
---@return FilterDocument|nil document nil if there is nothing to push.
function parser.parse(args, options)
	if args == "" or args == nil then return nil end

	local results = utils.split_parameters(args):map(parse_parameter)
	if tablex.find_if(results, function(result) return not result.readable end) ~= nil then return nil end

	local document = make_document(results, options or {})
	if next(document) == nil then return nil end

	local checked, reason = schemas.filter_document:validate(document)
	if not checked then error("Invalid filter document: " .. reason) end
	return checked
end

return parser
