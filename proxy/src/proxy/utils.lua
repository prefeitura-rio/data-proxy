-- Helpers that need no knowledge of the module that uses them.

local List = require("pl.List") --[[@as pl.ListClass]]
local constants = require("proxy.constants")
local crypto = require("resty.sha256")
local regex = require("proxy.regex")
local stringx = require("pl.stringx") --[[@as pl.stringx]]
local tablex = require("pl.tablex") --[[@as pl.tablex]]
local to_hex = require("resty.string").to_hex

--- Helper functions.
---@class Utils
local utils = {}

--- Percent-decode a text. A + character stays a + (it is not a space).
---
--- ```lua
--- utils.percent_decode("a%20b")  --> "a b"
--- utils.percent_decode("a+b")    --> "a+b"
--- utils.percent_decode("100%")   --> nil
--- ```
---@param value string Text with percent escapes, for example a part of a query string.
---@return string|nil decoded nil if a % is not followed by two hexadecimal digits.
function utils.percent_decode(value)
	for index = 1, #value do
		if value:sub(index, index) == "%" then
			local escape = value:sub(index + 1, index + constants.filter.percent_escape_digits)
			if not escape:match(regex.percent_escape) then return nil end
		end
	end
	local escaped = value:gsub("+", "%%2B")
	local ok, result = pcall(ngx.unescape_uri, escaped)
	if ok and type(result) == "string" then return result end
	return nil
end

--- Percent-encode a path. The / characters stay unchanged.
---
--- ```lua
--- utils.encode_path("/my table/1")  --> "/my%20table/1"
--- ```
---@param path string
---@return string encoded
function utils.encode_path(path) return (ngx.escape_uri(path):gsub(regex.escaped_slash, "/")) end

--- Check if a path is equal to one of the prefixes, or is below one of them.
---
--- ```lua
--- utils.matches_path_prefix("/access_policy", { "/access_policy" })    --> true
--- utils.matches_path_prefix("/access_policy/1", { "/access_policy" })  --> true
--- utils.matches_path_prefix("/access_policy_x", { "/access_policy" })  --> false
--- ```
---@param path string
---@param prefixes PathPrefixes|nil nil means no prefixes.
---@return boolean matches
function utils.matches_path_prefix(path, prefixes)
	local key = tablex.find_if(
		prefixes or {},
		function(prefix) return path == prefix or stringx.startswith(path, prefix .. "/") end
	)
	return key ~= nil
end

--- Make a new table with the entries of the source that have the listed names. A name without a value is left out.
---
--- ```lua
--- utils.pick({ A = "1", B = "2" }, { "A", "C" })  --> { A = "1" }
--- ```
---@param source HeaderMap
---@param names string[] The names to keep.
---@return HeaderMap picked
function utils.pick(source, names)
	return tablex.pairmap(function(_, name) return source[name], name end, names)
end

--- Decode a base64url text, the form that a JWT uses. The padding is optional.
---@param text string
---@return string|nil decoded nil if the text is not valid base64.
function utils.decode_base64url(text)
	local standard_base64 = text:gsub("-", "+"):gsub("_", "/")
	return ngx.decode_base64(standard_base64)
end

--- Make the SHA-256 digest of a text, in lower case hexadecimal.
---@param text string
---@return string digest 64 hexadecimal characters.
function utils.sha256_hex(text)
	local digest = crypto:new()
	digest:update(text)
	return to_hex(digest:final() --[[@as string]])
end

--- Check if a body has no data. An empty text, an empty JSON array, or null has no data.
---
--- ```lua
--- utils.is_empty_body("")      --> true
--- utils.is_empty_body("[]")    --> true
--- utils.is_empty_body("[1]")   --> false
--- ```
---@param body string
---@return boolean
function utils.is_empty_body(body) return body == "" or body == "[]" or body == "null" end

--- Check if a Content-Type is JSON, or is missing. Other types must contain the word json.
---
--- ```lua
--- utils.is_json_or_untyped("application/json; charset=utf-8")  --> true
--- utils.is_json_or_untyped(nil)                                --> true
--- utils.is_json_or_untyped("text/html")                        --> false
--- ```
---@param content_type string|nil Value of the Content-Type header.
---@return boolean
function utils.is_json_or_untyped(content_type)
	local lowered = (content_type or ""):lower()
	return lowered == "" or stringx.lfind(lowered, "json") ~= nil
end

--- Check if a body is larger than the limit. A limit of 0 means no limit.
---
--- ```lua
--- utils.exceeds_body_limit("abcdef", 5)  --> true
--- utils.exceeds_body_limit("abcde", 5)   --> false
--- utils.exceeds_body_limit("abcdef", 0)  --> false
--- ```
---@param body string
---@param max_body number Largest size in bytes. 0 means no limit.
---@return boolean
function utils.exceeds_body_limit(body, max_body) return max_body > 0 and #body > max_body end

--- Split a query string into its parameters. Empty parts are dropped.
---
--- ```lua
--- utils.split_parameters("id=eq.1&&name=eq.Rio")  --> { "id=eq.1", "name=eq.Rio" }
--- ```
---@param args string The raw query string.
---@return pl.List<string> parameters
function utils.split_parameters(args)
	return List(stringx.split(args, constants.filter.query_parameter_separator)):filter(
		function(parameter) return parameter ~= "" end
	)
end

return utils
