---@diagnostic disable: missing-fields

local helpers = require("tests.unit.helpers")
local postgrest = require("proxy.postgrest")

local check = helpers.check(describe, it)

local with_retry = { retry_postgrest = "http://write" }

check("postgrest.should_retry", function(case) return postgrest.should_retry(case.context, case.response) end, {
	{ name = "retries a 502", context = with_retry, response = { status = 502 }, expected = true },
	{ name = "retries a 503", context = with_retry, response = { status = 503 }, expected = true },
	{ name = "retries a 504", context = with_retry, response = { status = 504 }, expected = true },
	{ name = "retries when the attempt failed", context = with_retry, response = nil, expected = true },
	{ name = "does not retry a 200", context = with_retry, response = { status = 200 }, expected = false },
	{ name = "does not retry a 404", context = with_retry, response = { status = 404 }, expected = false },
	{ name = "does not retry a 500", context = with_retry, response = { status = 500 }, expected = false },
	{
		name = "does not retry without a retry service",
		context = {},
		response = { status = 503 },
		expected = false,
	},
	{ name = "does not retry a failure without a retry service", context = {}, response = nil, expected = false },
})

check("postgrest.forwarded_headers", function(case) return postgrest.forwarded_headers(case.headers) end, {
	{
		name = "keeps the allowlisted headers",
		headers = { ["Content-Type"] = "application/json", ["X-Source"] = "ducklake", ["Location"] = "/a" },
		expected = { ["Content-Type"] = "application/json", ["X-Source"] = "ducklake", ["Location"] = "/a" },
	},
	{
		name = "drops a header that is not allowlisted",
		headers = { ["Set-Cookie"] = "a=1", ["X-Source"] = "ducklake" },
		expected = { ["X-Source"] = "ducklake" },
	},
	{ name = "gives an empty table for no headers", headers = {}, expected = {} },
	{ name = "gives an empty table if no header is allowlisted", headers = { Server = "x" }, expected = {} },
})
