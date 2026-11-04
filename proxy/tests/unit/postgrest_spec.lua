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

describe("postgrest.query with an aggregate", function()
	local aggregate = {
		method = "GET",
		uri = "/people",
		args = "select=a,n:b.sum()",
		started = 0,
		postgrest = "http://postgrest",
		aggregate_path = "/rpc/people_aggregate",
	}

	--- Make a sender that gives the answers in order, and records the uri and the args of each call.
	local function recording(answers)
		local seen = {}
		local function sender(context)
			seen[#seen + 1] = { uri = context.uri, args = context.args }
			local answer = answers[#seen]
			if answer == false then return nil, "failed" end
			return answer
		end
		return sender, seen
	end

	local function no_sleep() end

	it("sends the aggregate function first, without the query string, and returns its answer", function()
		local sender, seen = recording({ { status = 200, body = '[{"a":1,"n":5}]' } })

		local response = postgrest.query(aggregate, sender, no_sleep)

		assert.same({ status = 200, body = '[{"a":1,"n":5}]' }, response)
		assert.same({ { uri = "/rpc/people_aggregate", args = "" } }, seen)
	end)

	it("returns a partial answer of the function, so the cap of the function reaches the client", function()
		local partial = { status = 206, body = '[{"a":1,"n":5}]', headers = { ["Content-Range"] = "0-0/*" } }
		local sender, seen = recording({ partial })

		local response = postgrest.query(aggregate, sender, no_sleep)

		assert.same(partial, response)
		assert.same(1, #seen)
	end)

	it("sends the original request when the function answers null", function()
		local sender, seen = recording({ { status = 200, body = "null" }, { status = 200, body = "[1]" } })

		local response = postgrest.query(aggregate, sender, no_sleep)

		assert.same({ status = 200, body = "[1]" }, response)
		assert.same({ "/rpc/people_aggregate", "/people" }, { seen[1].uri, seen[2].uri })
		assert.same("select=a,n:b.sum()", seen[2].args)
	end)

	it("treats a null with spaces and a new line as null", function()
		local sender, seen = recording({ { status = 200, body = " null\n" }, { status = 200, body = "[1]" } })

		postgrest.query(aggregate, sender, no_sleep)

		assert.same(2, #seen)
	end)

	for status, label in pairs({ [404] = "404", [500] = "500", [503] = "503", [401] = "401" }) do
		it("sends the original request when the function answers " .. label, function()
			local sender, seen = recording({ { status = status, body = "{}" }, { status = 200, body = "[1]" } })

			local response = postgrest.query(aggregate, sender, no_sleep)

			assert.same({ status = 200, body = "[1]" }, response)
			assert.same({ "/rpc/people_aggregate", "/people" }, { seen[1].uri, seen[2].uri })
		end)
	end

	it("sends the original request when the call to the function fails", function()
		local sender, seen = recording({ false, { status = 200, body = "[1]" } })

		local response = postgrest.query(aggregate, sender, no_sleep)

		assert.same({ status = 200, body = "[1]" }, response)
		assert.same(2, #seen)
	end)

	it("does not call the function for a request without an aggregate path", function()
		local sender, seen = recording({ { status = 200, body = "[1]" } })

		postgrest.query(read, sender, no_sleep)

		assert.same({ "/people" }, { seen[1].uri })
		assert.same(1, #seen)
	end)

	it("keeps the retries of the original request after the function falls back", function()
		local sender, seen = recording({
			{ status = 200, body = "null" },
			{ status = 503, body = "" },
			{ status = 200, body = "[1]" },
		})

		local response = postgrest.query(aggregate, sender, no_sleep)

		assert.same({ status = 200, body = "[1]" }, response)
		assert.same(3, #seen)
	end)
end)

describe("postgrest.query with a count", function()
	local counted = {
		method = "GET",
		uri = "/people",
		args = "limit=20&offset=40",
		started = 0,
		postgrest = "http://postgrest",
		headers = { Prefer = "count=exact" },
		count = {
			path = "/rpc/people_aggregate",
			count_headers = { ["X-DuckLake-Filter"] = "count" },
			page_headers = { ["X-DuckLake-Filter"] = "page" },
		},
	}

	local total = { status = 200, body = '[{"total":1281450}]', headers = { ["X-DuckLake-Snapshot"] = "239" } }
	local function page(range, status)
		return {
			status = status or 200,
			body = "[1]",
			headers = { ["Content-Range"] = range, ["X-Source"] = "ducklake", ["X-DuckLake-Snapshot"] = "239" },
		}
	end

	--- Make a sender that gives the answers in order, and records the uri and the headers of each call.
	local function recording(answers)
		local seen = {}
		local function sender(context)
			seen[#seen + 1] = { uri = context.uri, args = context.args, headers = context.headers }
			local answer = answers[#seen]
			if answer == false then return nil, "failed" end
			return answer
		end
		return sender, seen
	end

	local function no_sleep() end

	it("counts first, then reads the page at the snapshot of the count", function()
		local sender, seen = recording({ total, page("40-59/*") })

		postgrest.query(counted, sender, no_sleep)

		assert.same({ "/rpc/people_aggregate", "/people" }, { seen[1].uri, seen[2].uri })
		assert.same({ "", "limit=20&offset=40" }, { seen[1].args, seen[2].args })
		assert.same({ ["X-DuckLake-Filter"] = "count" }, seen[1].headers)
		assert.same({ ["X-DuckLake-Filter"] = "page", ["X-DuckLake-Snapshot"] = "239" }, seen[2].headers)
		assert.same("count=exact", counted.headers.Prefer)
	end)

	it("gives 206 and the range with the total for a page inside the rows", function()
		local sender = recording({ total, page("40-59/*") })

		local response = assert(postgrest.query(counted, sender, no_sleep))

		assert.same(206, response.status)
		assert.same("40-59/1281450", response.headers["Content-Range"])
		assert.same("count=exact", response.headers["Preference-Applied"])
		assert.same("ducklake", response.headers["X-Source"])
		assert.same("[1]", response.body)
	end)

	it("gives 200 for a page that holds every row", function()
		local sender = recording({
			{ status = 200, body = '[{"total":3}]', headers = { ["X-DuckLake-Snapshot"] = "239" } },
			page("0-2/*"),
		})

		local response = assert(postgrest.query(counted, sender, no_sleep))

		assert.same(200, response.status)
		assert.same("0-2/3", response.headers["Content-Range"])
	end)

	it("gives 206 for a first page that does not hold every row", function()
		local sender = recording({
			{ status = 200, body = '[{"total":30}]', headers = { ["X-DuckLake-Snapshot"] = "239" } },
			page("0-19/*"),
		})

		local response = assert(postgrest.query(counted, sender, no_sleep))

		assert.same(206, response.status)
		assert.same("0-19/30", response.headers["Content-Range"])
	end)

	local original = { status = 200, body = "[2]", headers = {} }
	local fallbacks = {
		{ name = "the function answers null", answers = { { status = 200, body = "null", headers = {} }, original } },
		{ name = "the function fails", answers = { { status = 500, body = "{}", headers = {} }, original } },
		{ name = "the call fails", answers = { false, original } },
		{
			name = "the answer has no snapshot",
			answers = { { status = 200, body = '[{"total":5}]', headers = {} }, original },
		},
		{
			name = "the total is not a whole number",
			answers = {
				{ status = 200, body = '[{"total":1.5}]', headers = { ["X-DuckLake-Snapshot"] = "1" } },
				original,
			},
		},
		{ name = "the page is empty", answers = { total, page("*/*"), original } },
		{ name = "the page is beyond the total", answers = { total, page("1281450-1281459/*"), original } },
	}
	for _, case in ipairs(fallbacks) do
		it("sends the original request when " .. case.name, function()
			local sender, seen = recording(case.answers)

			local response = assert(postgrest.query(counted, sender, no_sleep))

			assert.same(original, response)
			assert.same("/people", seen[#seen].uri)
			assert.same({ Prefer = "count=exact" }, seen[#seen].headers)
		end)
	end

	it("gives a page that is not a 200 as it is", function()
		local failed = { status = 400, body = "{}", headers = {} }
		local sender, seen = recording({ total, failed })

		local response = assert(postgrest.query(counted, sender, no_sleep))

		assert.same(failed, response)
		assert.same(2, #seen)
	end)

	it("sends the page again after a 503", function()
		local sender, seen = recording({ total, { status = 503, body = "", headers = {} }, page("40-59/*") })

		local response = assert(postgrest.query(counted, sender, no_sleep))

		assert.same(206, response.status)
		assert.same(3, #seen)
	end)

	it("gives the failure of the page when every attempt fails", function()
		local sender = recording({ total, false, false, false })

		local response, response_error = postgrest.query(counted, sender, no_sleep)

		assert.is_nil(response)
		assert.same("failed", response_error)
	end)

	it("does not count a request without a count plan", function()
		local sender, seen = recording({ original })

		postgrest.query({ method = "GET", uri = "/people", args = "", started = 0, postgrest = "x" }, sender, no_sleep)

		assert.same(1, #seen)
	end)
end)

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
