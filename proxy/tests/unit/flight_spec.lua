local constants = require("proxy.constants")
local flight = require("proxy.flight")
local helpers = require("tests.unit.helpers")

local check = helpers.check(describe, it)

--- A module that works like resty.lock: new makes a lock, lock gives the waited seconds, and unlock frees it.
---@param case table
---@return table module
---@return table calls
local function lock_module_with(case)
	local calls = { new = 0, lock = 0, unlock = 0 }
	local lock = {}
	function lock.lock(_, key)
		calls.lock = calls.lock + 1
		calls.key = key
		return case.waited, case.lock_error
	end
	function lock.unlock()
		calls.unlock = calls.unlock + 1
		return 1
	end
	local module = {}
	function module.new(_, dict, options)
		calls.new = calls.new + 1
		calls.dict = dict
		calls.options = options
		if case.new_fails then return nil, "dictionary not found" end
		return lock
	end
	return module, calls
end

check("flight.enter", function(case)
	local module, calls = lock_module_with(case)
	local group = flight.new(module)
	return { role = group:enter("k"), locks = calls.lock, unlocks = calls.unlock }
end, {
	{
		name = "makes the first request the leader, and keeps the lock while it asks PostgREST",
		waited = 0,
		expected = { role = "leader", locks = 1, unlocks = 0 },
	},
	{
		name = "makes a request that waited a follower, and frees the lock at once for the others",
		waited = 0.25,
		expected = { role = "follower", locks = 1, unlocks = 1 },
	},
	{
		name = "lets a request that timed out ask PostgREST itself",
		lock_error = "timeout",
		expected = { role = "alone", locks = 1, unlocks = 0 },
	},
	{
		name = "lets a request that got a lock error ask PostgREST itself",
		lock_error = "no memory",
		expected = { role = "alone", locks = 1, unlocks = 0 },
	},
	{
		name = "lets every request ask PostgREST when the dictionary is missing",
		new_fails = true,
		expected = { role = "alone", locks = 0, unlocks = 0 },
	},
})

describe("flight.enter", function()
	it("locks the cache key in the dictionary and with the limits of the constants", function()
		local module, calls = lock_module_with({ waited = 0 })

		flight.new(module):enter("the-key")

		assert.equal("the-key", calls.key)
		assert.equal(constants.locks.dict, calls.dict)
		assert.same(constants.locks.options, calls.options)
	end)
end)

describe("flight.leave", function()
	it("frees the lock of the leader, once", function()
		local module, calls = lock_module_with({ waited = 0 })
		local group = flight.new(module)
		group:enter("k")

		group:leave()
		group:leave()

		assert.equal(1, calls.unlock)
	end)

	it("frees nothing for a follower, because it freed the lock at once", function()
		local module, calls = lock_module_with({ waited = 0.1 })
		local group = flight.new(module)
		group:enter("k")

		group:leave()

		assert.equal(1, calls.unlock)
	end)

	it("frees nothing for a request that holds no lock", function()
		local module, calls = lock_module_with({ lock_error = "timeout" })
		local group = flight.new(module)
		group:enter("k")

		group:leave()

		assert.equal(0, calls.unlock)
	end)
end)
