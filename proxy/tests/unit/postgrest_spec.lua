---@diagnostic disable: missing-fields

local helpers = require("tests.unit.helpers")
local postgrest = require("proxy.postgrest")

local check = helpers.check(describe, it)

describe("postgrest.query", function()
	it("sends the request once, to the PostgREST address of the context", function()
		local calls = {}
		local answer = { status = 503 }
		local response = postgrest.query({ postgrest = "http://postgrest" }, function(context, base)
			calls[#calls + 1] = { context = context, base = base }
			return answer
		end)

		assert.same(answer, response)
		assert.same(1, #calls)
		assert.same("http://postgrest", calls[1].base)
	end)
end)

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
