local helpers = require("tests.unit.helpers")
local utils = require("proxy.utils")

local check = helpers.check(describe, it)

check("utils.percent_decode", function(case) return utils.percent_decode(case.value) end, {
	{ name = "decodes an escape", value = "a%20b", expected = "a b" },
	{ name = "decodes an escape in lower case hex", value = "%2f", expected = "/" },
	{ name = "keeps a plus character", value = "a+b", expected = "a+b" },
	{ name = "keeps text without escapes", value = "plain", expected = "plain" },
	{ name = "decodes an empty text", value = "", expected = "" },
	{ name = "gives nil for a percent that ends the text", value = "100%", expected = helpers.NULL },
	{ name = "gives nil for a percent with one digit", value = "a%2", expected = helpers.NULL },
	{ name = "gives nil for an escape that is not hexadecimal", value = "%zz", expected = helpers.NULL },
})

check("utils.encode_path", function(case) return utils.encode_path(case.value) end, {
	{ name = "escapes reserved characters", value = "/table?x", expected = "/table%3Fx" },
	{ name = "escapes spaces and keeps the separators", value = "/my table/1", expected = "/my%20table/1" },
	{ name = "keeps a plain path", value = "/people", expected = "/people" },
})

check("utils.matches_path_prefix", function(case) return utils.matches_path_prefix(case.path, case.prefixes) end, {
	{ name = "matches the prefix itself", path = "/access_policy", prefixes = { "/access_policy" }, expected = true },
	{
		name = "matches a path below the prefix",
		path = "/access_policy/1",
		prefixes = { "/access_policy" },
		expected = true,
	},
	{
		name = "does not match a sibling that starts with the prefix",
		path = "/access_policy_x",
		prefixes = { "/access_policy" },
		expected = false,
	},
	{ name = "does not match a path above the prefix", path = "/", prefixes = { "/access_policy" }, expected = false },
	{ name = "matches the second prefix", path = "/b/1", prefixes = { "/a", "/b" }, expected = true },
	{ name = "does not match an empty list", path = "/a", prefixes = {}, expected = false },
	{ name = "does not match nil", path = "/a", prefixes = nil, expected = false },
})

check("utils.pick", function(case) return utils.pick(case.source, case.names) end, {
	{ name = "keeps only the listed names", source = { A = "1", B = "2" }, names = { "A" }, expected = { A = "1" } },
	{ name = "leaves out a name without a value", source = { A = "1" }, names = { "A", "C" }, expected = { A = "1" } },
	{ name = "gives an empty table for no names", source = { A = "1" }, names = {}, expected = {} },
	{ name = "gives an empty table for an empty source", source = {}, names = { "A" }, expected = {} },
	{ name = "keeps an empty text", source = { A = "" }, names = { "A" }, expected = { A = "" } },
})

check("utils.is_empty_body", function(case) return utils.is_empty_body(case.body) end, {
	{ name = "has no data for an empty text", body = "", expected = true },
	{ name = "has no data for an empty JSON array", body = "[]", expected = true },
	{ name = "has no data for null", body = "null", expected = true },
	{ name = "has data in an empty JSON object", body = "{}", expected = false },
	{ name = "has data in a JSON array with an item", body = "[1]", expected = false },
	{ name = "has data in a text", body = "x", expected = false },
})

check("utils.is_json_or_untyped", function(case) return utils.is_json_or_untyped(case.value) end, {
	{ name = "accepts a missing type", value = nil, expected = true },
	{ name = "accepts an empty type", value = "", expected = true },
	{ name = "accepts application/json", value = "application/json", expected = true },
	{ name = "accepts a type with a charset", value = "application/json; charset=utf-8", expected = true },
	{ name = "accepts a type that ends with +json", value = "application/vnd.pgrst.object+json", expected = true },
	{ name = "accepts upper case", value = "APPLICATION/JSON", expected = true },
	{ name = "rejects text/html", value = "text/html", expected = false },
	{ name = "rejects text/csv", value = "text/csv", expected = false },
})

check("utils.exceeds_body_limit", function(case) return utils.exceeds_body_limit(case.body, case.max_body) end, {
	{ name = "has no limit if max_body is 0", body = "abcdef", max_body = 0, expected = false },
	{ name = "accepts a body equal to the limit", body = "abcde", max_body = 5, expected = false },
	{ name = "rejects a body one byte over the limit", body = "abcdef", max_body = 5, expected = true },
	{ name = "accepts an empty body", body = "", max_body = 5, expected = false },
})

-- The base64 double keeps the text, so these cases pin the url-safe mapping and the nil path,
-- which are the parts of utils.decode_base64url that belong to the proxy.
check("utils.decode_base64url", function(case) return utils.decode_base64url(case.value) end, {
	{ name = "maps the url-safe characters to the standard ones", value = "a-b_c", expected = "a+b/c" },
	{ name = "keeps text without url-safe characters", value = "aGk", expected = "aGk" },
	{ name = "gives nil when the decoder gives nil", value = "!!!", expected = helpers.NULL },
})

check("utils.split_parameters", function(case) return utils.split_parameters(case.args) end, {
	{ name = "splits on &", args = "id=eq.1&name=eq.Rio", expected = { "id=eq.1", "name=eq.Rio" } },
	{ name = "drops empty parts", args = "a=1&&b=2&", expected = { "a=1", "b=2" } },
	{ name = "gives one part for one parameter", args = "a=1", expected = { "a=1" } },
	{ name = "gives no parts for an empty text", args = "", expected = {} },
})
