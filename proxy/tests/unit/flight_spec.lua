local flight = require("proxy.flight")
local helpers = require("tests.unit.helpers")

local check = helpers.check(describe, it)

--- A shared dictionary that holds keys like ngx.shared: add refuses a key that exists.
---@return table dict
local function dict_with()
	local dict = { values = {}, ttls = {} }
	function dict:add(key, value, ttl)
		if self.values[key] ~= nil then return false, "exists" end
		self.values[key] = value
		self.ttls[key] = ttl
		return true
	end
	function dict:get(key) return self.values[key] end
	function dict:delete(key) self.values[key] = nil end
	return dict
end

--- A clock that moves when a request sleeps, and a hook that runs at each sleep.
---@param on_sleep fun(count: integer)|nil
---@return table options
local function clock_with(on_sleep)
	local state = { now = 0, sleeps = 0 }
	return {
		state = state,
		ttl = 60,
		poll = 0.5,
		max_wait = 5,
		now = function() return state.now end,
		sleep = function(seconds)
			state.now = state.now + seconds
			state.sleeps = state.sleeps + 1
			if on_sleep then on_sleep(state.sleeps) end
		end,
	}
end

check("flight.lead", function(case)
	local dict = nil
	if not case.no_dict then dict = dict_with() end
	local options = clock_with()
	local group = flight.new(dict, options)
	local answers = {}
	for _, step in ipairs(case.steps) do
		if step.release then
			group:release(step.release)
		else
			answers[#answers + 1] = group:lead(step.lead)
		end
	end
	return answers
end, {
	{
		name = "lets the first request for a key lead and the next ones follow",
		steps = { { lead = "k" }, { lead = "k" }, { lead = "k" } },
		expected = { true, false, false },
	},
	{
		name = "gives each key its own leader",
		steps = { { lead = "a" }, { lead = "b" } },
		expected = { true, true },
	},
	{
		name = "lets the next request lead after the leader releases the key",
		steps = { { lead = "k" }, { release = "k" }, { lead = "k" } },
		expected = { true, true },
	},
	{
		name = "lets every request lead when there is no shared dictionary",
		no_dict = true,
		steps = { { lead = "k" }, { lead = "k" } },
		expected = { true, true },
	},
})

describe("flight.lead", function()
	it("makes the lock expire, so a leader that dies doesn't block the key", function()
		local dict = dict_with()
		local group = flight.new(dict, clock_with())

		group:lead("k")

		assert.equal(60, dict.ttls["flight:k"])
	end)
end)

describe("flight.wait", function()
	it("returns as soon as the leader releases the key", function()
		local dict = dict_with()
		local options
		options = clock_with(function(count)
			if count == 3 then dict:delete("flight:k") end
		end)
		local group = flight.new(dict, options)
		group:lead("k")

		group:wait("k")

		assert.equal(3, options.state.sleeps)
	end)

	it("returns at once when nobody leads the key", function()
		local options = clock_with()
		local group = flight.new(dict_with(), options)

		group:wait("k")

		assert.equal(0, options.state.sleeps)
	end)

	it("stops at the longest wait when the leader never releases", function()
		local dict = dict_with()
		local options = clock_with()
		local group = flight.new(dict, options)
		group:lead("k")

		group:wait("k")

		assert.equal(10, options.state.sleeps)
	end)

	it("returns at once when there is no shared dictionary", function()
		local options = clock_with()
		local group = flight.new(nil, options)

		group:wait("k")

		assert.equal(0, options.state.sleeps)
	end)
end)
