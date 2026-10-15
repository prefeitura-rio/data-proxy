-- Let one request ask PostgREST for a cache key, while the other requests for the same key wait for its answer.

local constants = require("proxy.constants")

--- The functions that decide which request leads for a cache key.
---@class Flight
---@field dict table|nil The shared dictionary with add, get and delete. nil means that every request leads.
---@field ttl number Seconds after which a lock expires, so a leader that dies doesn't block the key.
---@field poll number Seconds between two looks at the lock.
---@field max_wait number Most seconds that a follower waits for the leader.
---@field now fun(): number
---@field sleep fun(seconds: number)
local flight = {}
flight.__index = flight

--- Make a flight over a shared dictionary.
---@param dict table|nil The shared dictionary. nil gives no coalescing.
---@param options table|nil Replace the constants, the clock or the sleep. The specs use it.
---@return Flight
function flight.new(dict, options)
	local settings = constants.flight
	options = options or {}
	return setmetatable({
		dict = dict,
		ttl = options.ttl or settings.ttl_seconds,
		poll = options.poll or settings.poll_seconds,
		max_wait = options.max_wait or settings.max_wait_seconds,
		now = options.now or ngx.now,
		sleep = options.sleep or ngx.sleep,
	}, flight)
end

--- The name of the lock of a key.
---@param key string
---@return string
local function lock_name(key) return constants.flight.prefix .. key end

--- Take the lock of a key, if no other request holds it.
---
--- ```lua
--- flight:lead("k")  --> true    (this request asks PostgREST)
--- flight:lead("k")  --> false   (another request leads, so this one waits)
--- ```
---@param key string Cache key.
---@return boolean leading
function flight:lead(key)
	if self.dict == nil then return true end
	return self.dict:add(lock_name(key), true, self.ttl) == true
end

--- Free the lock of a key.
---@param key string Cache key.
---@return nil
function flight:release(key)
	if self.dict ~= nil then self.dict:delete(lock_name(key)) end
end

--- Wait until the leader frees the lock of a key, or until the longest wait passes.
---@param key string Cache key.
---@return nil
function flight:wait(key)
	if self.dict == nil then return end
	local deadline = self.now() + self.max_wait
	while self.dict:get(lock_name(key)) ~= nil and self.now() < deadline do
		self.sleep(self.poll)
	end
end

return flight
