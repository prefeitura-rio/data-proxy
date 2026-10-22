---@diagnostic disable: missing-fields

local helpers = require("tests.unit.helpers")
local postgrest = require("proxy.postgrest")

local check = helpers.check(describe, it)

local read = { method = "GET", uri = "/people", args = "", started = 0, postgrest = "http://postgrest" }
local write = { method = "POST", uri = "/people", args = "", started = 0, postgrest = "http://postgrest" }

check("postgrest.should_retry", function(case) return postgrest.should_retry(case.context, case.response) end, {
	{ name = "retries a read after a 502", context = read, response = { status = 502 }, expected = true },
	{ name = "retries a read after a 503", context = read, response = { status = 503 }, expected = true },
	{ name = "retries a read after a 504", context = read, response = { status = 504 }, expected = true },
	{ name = "retries a read that failed", context = read, response = nil, expected = true },
	{ name = "retries a HEAD", context = { method = "HEAD" }, response = { status = 503 }, expected = true },
	{ name = "does not retry a 200", context = read, response = { status = 200 }, expected = false },
	{ name = "does not retry a 404", context = read, response = { status = 404 }, expected = false },
	{ name = "does not retry a 500", context = read, response = { status = 500 }, expected = false },
	{ name = "does not retry a write after a 503", context = write, response = { status = 503 }, expected = false },
	{ name = "does not retry a write that failed", context = write, response = nil, expected = false },
})

--- Make a sender that gives the answers in order, and a sleeper that records the waits.
---@param answers (table|false)[] The answer of each call. A false stands for a failed call.
---@return function sender
---@return function sleeper
---@return table calls The base address of each call.
---@return table waits The milliseconds of each wait.
local function fakes(answers)
	local calls, waits = {}, {}
	local function sender(_, base)
		calls[#calls + 1] = base
		local answer = answers[#calls]
		if answer == false then return nil, "failed" end
		return answer
	end
	local function sleeper(ms) waits[#waits + 1] = ms end
	return sender, sleeper, calls, waits
end

describe("postgrest.query", function()
	it("sends one request to the PostgREST address of the context", function()
		local sender, sleeper, calls = fakes({ { status = 200 } })

		local response = postgrest.query(read, sender, sleeper)

		assert.same({ status = 200 }, response)
		assert.same({ "http://postgrest" }, calls)
	end)

	it("sends a read again after a 503, and waits first", function()
		local sender, sleeper, calls, waits = fakes({ { status = 503 }, { status = 200 } })

		local response = postgrest.query(read, sender, sleeper)

		assert.same({ status = 200 }, response)
		assert.same(2, #calls)
		assert.same({ 500 }, waits)
	end)

	it("sends a read that fails again, twice at most, and waits twice as long each time", function()
		local sender, sleeper, calls, waits = fakes({ false, { status = 502 }, { status = 503 } })

		local response, response_error = postgrest.query(read, sender, sleeper)

		assert.same({ status = 503 }, response)
		assert.is_nil(response_error)
		assert.same(3, #calls)
		assert.same({ 500, 1000 }, waits)
	end)

	it("gives the reason of the last failure when every attempt fails", function()
		local sender, sleeper = fakes({ false, false, false })

		local response, response_error = postgrest.query(read, sender, sleeper)

		assert.is_nil(response)
		assert.same("failed", response_error)
	end)

	it("sends a write once, because a second write could change the data twice", function()
		local sender, sleeper, calls, waits = fakes({ { status = 503 } })

		local response = postgrest.query(write, sender, sleeper)

		assert.same({ status = 503 }, response)
		assert.same(1, #calls)
		assert.same({}, waits)
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
