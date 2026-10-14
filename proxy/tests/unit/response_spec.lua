---@diagnostic disable: missing-fields

local helpers = require("tests.unit.helpers")
local response = require("proxy.response")

local check = helpers.check(describe, it)

local UNAVAILABLE_BODY = '{"error":"PostgREST unavailable"}'

check("response.plan", function(case) return response.plan(case.result) end, {
	{
		name = "plans a 502 with the exact error body when PostgREST is unavailable",
		result = { response = nil, source = "none" },
		expected = {
			status = 502,
			body = UNAVAILABLE_BODY,
			headers = {},
			log_fields = { status = 502, source = "none", bytes = #UNAVAILABLE_BODY },
		},
	},
	{
		name = "plans a 503 with a retry delay when the proxy is overloaded",
		result = { response = nil, source = "none", error = "overloaded" },
		expected = {
			status = 503,
			body = '{"error":"Proxy overloaded"}',
			headers = { ["Retry-After"] = "1" },
			default_content_type = "application/json; charset=utf-8",
			log_fields = { status = 503, source = "none", bytes = 28, error = "overloaded" },
		},
	},
	{
		name = "plans a PostgREST answer as a cache miss",
		result = { response = { status = 200, body = "[1]", headers = {} }, source = "postgrest" },
		expected = {
			status = 200,
			body = "[1]",
			headers = {},
			x_cache = "MISS",
			default_content_type = "application/json; charset=utf-8",
			log_fields = { status = 200, source = "postgrest", bytes = 3 },
		},
	},
	{
		name = "plans a PostgREST error answer as a cache miss",
		result = { response = { status = 404, body = "[]", headers = {} }, source = "postgrest" },
		expected = {
			status = 404,
			body = "[]",
			headers = {},
			x_cache = "MISS",
			default_content_type = "application/json; charset=utf-8",
			log_fields = { status = 404, source = "postgrest", bytes = 2 },
		},
	},
	{
		name = "plans a cached answer as a cache hit",
		result = { response = { status = 200, body = "[1]", headers = {} }, source = "cache" },
		expected = {
			status = 200,
			body = "[1]",
			headers = {},
			x_cache = "HIT",
			default_content_type = "application/json; charset=utf-8",
			log_fields = { status = 200, source = "cache", bytes = 3 },
		},
	},
	{
		name = "logs the X-Source header as the source",
		result = {
			response = { status = 200, body = "[1]", headers = { ["X-Source"] = "ducklake" } },
			source = "postgrest",
		},
		expected = {
			status = 200,
			body = "[1]",
			headers = { ["X-Source"] = "ducklake" },
			x_cache = "MISS",
			default_content_type = "application/json; charset=utf-8",
			log_fields = { status = 200, source = "ducklake", bytes = 3 },
		},
	},
})
