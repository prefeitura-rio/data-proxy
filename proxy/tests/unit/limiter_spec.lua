local helpers = require("tests.unit.helpers")
local limiter = require("proxy.limiter")

local check = helpers.check(describe, it)

--- A shared dictionary that counts like ngx.shared: incr adds to a number and starts it at init.
---@param start integer|nil The count at the start. nil means that the key is not set.
---@return table dict
local function dict_with(start)
	local values = { inflight = start }
	return {
		values = values,
		incr = function(_, key, delta, init)
			values[key] = (values[key] or init) + delta
			return values[key]
		end,
	}
end

--- Run a task that gives one answer through the limiter, and report what happened.
---@param case table
---@return table outcome
local function outcome_of(case)
	local dict = nil
	if not case.no_dict then dict = dict_with(case.start) end
	local ran = false
	local first, second = limiter.run(dict, case.limit, function()
		ran = true
		if case.nested then
			local inner_ran = false
			local inner_first, inner_second = limiter.run(dict, case.limit, function()
				inner_ran = true
				return "inner"
			end)
			return { inner_ran = inner_ran, inner_first = inner_first, inner_second = inner_second }
		end
		return "answer", "reason"
	end)
	return { ran = ran, first = first, second = second, count = dict and dict.values.inflight or nil }
end

check("limiter.run", outcome_of, {
	{
		name = "runs the task below the limit and frees the slot",
		limit = 2,
		start = 0,
		expected = { ran = true, first = "answer", second = "reason", count = 0 },
	},
	{
		name = "rejects the task at the limit and keeps the count",
		limit = 2,
		start = 2,
		expected = { ran = false, second = "overloaded", count = 2 },
	},
	{
		name = "runs the task when the limit is 0 and does not count",
		limit = 0,
		start = 5,
		expected = { ran = true, first = "answer", second = "reason", count = 5 },
	},
	{
		name = "runs the task when there is no shared dictionary",
		limit = 2,
		no_dict = true,
		expected = { ran = true, first = "answer", second = "reason" },
	},
	{
		name = "rejects a second task that starts while the first one runs",
		limit = 1,
		start = 0,
		nested = true,
		expected = {
			ran = true,
			first = { inner_ran = false, inner_second = "overloaded" },
			count = 0,
		},
	},
})

describe("limiter.run", function()
	it("frees the slot and raises the error when the task fails", function()
		local dict = dict_with(0)

		local ok, failure = pcall(limiter.run, dict, 2, function() error("broken", 0) end)

		assert.is_false(ok)
		assert.equal("broken", failure)
		assert.equal(0, dict.values.inflight)
	end)
end)
