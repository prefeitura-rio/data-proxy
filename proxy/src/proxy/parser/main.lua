-- Parse PostgREST filter parameters into the filter document of the X-DuckLake-Filter header.
--
-- ```lua
-- parser.parse("id=eq.42&order=id.desc")  --> { where = { kind = "comparison", column = "id", ... } }
-- parser.parse("id=eq.42&name=fts.x")     --> { where = { kind = "comparison", column = "id", ... } }
-- parser.parse("select=id,name")          --> nil
-- ```

local constants = require("proxy.constants")
local patterns = require("proxy.parser.patterns")
local regex = require("proxy.regex")
local schemas = require("proxy.schemas.main")
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
	if constants.filter.non_filter_keys[key] then return SKIPPED end

	if constants.filter.group_keys[key] then
		if value:sub(1, 1) ~= "(" then return SKIPPED end
		return make_parameter_result(patterns.filter_grammar:match(key .. value))
	end

	if not patterns.whole_column_name:match(key) then return SKIPPED end
	return make_parameter_result(patterns.filter_grammar:match(key .. "." .. value))
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

--- Parse the query string of a request into the filter document.
---
--- The parser ignores parameters that are not filters (select, order, limit, unknown keys).
--- It also ignores filters that the grammar does not accept.
--- If a percent escape is not valid, the function returns nil. The proxy cannot read the request correctly.
---
--- ```lua
--- parser.parse("id=eq.42")
--- --> { where = { kind = "comparison", column = "id", operator = "eq", negated = false, value = "42" } }
--- parser.parse("id=eq.1&name=eq.Rio") --> { where = { kind = "group", operator = "and", items = { ... } } }
--- parser.parse("select=id,name")      --> nil
--- ```
---@param args string|nil The raw query string, for example id=eq.1&name=like.*rio*.
---@return FilterDocument|nil document nil if no parameter is a supported filter.
function parser.parse(args)
	if args == "" or args == nil then return nil end

	local results = utils.split_parameters(args):map(parse_parameter)
	if tablex.find_if(results, function(result) return not result.readable end) ~= nil then return nil end

	local nodes = results
		:filter(function(result) return result.node ~= nil end)
		:map(function(result) return result.node end)

	local document = make_filter_document(nodes)
	if document == nil then return nil end

	local checked, reason = schemas.filter_document:validate(document)
	if not checked then error("Invalid filter document: " .. reason) end
	return checked
end

return parser
