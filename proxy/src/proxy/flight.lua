-- Let one request ask PostgREST for a cache key, while the other requests for the same key wait for it.
-- The lock is resty.lock, which OpenResty ships. It lives in the shared dictionary of the nginx config.

local constants = require("proxy.constants")

--- One flight for each request. It says what the request is for a cache key, and frees the lock of a leader.
---@class Flight
---@field lock_module table Works like resty.lock: new gives a lock, and a lock has lock and unlock.
---@field held table|nil The lock that this request holds as the leader.
local flight = {}
flight.__index = flight

--- Make the flight of one request.
---@param lock_module table|nil Replaces resty.lock. The specs use it.
---@return Flight
function flight.new(lock_module) return setmetatable({ lock_module = lock_module or require("resty.lock") }, flight) end

--- Take part in the flight for a cache key.
---
--- ```lua
--- flight:enter("k")  --> "leader"    (first: ask PostgREST, store the answer, then call leave)
--- flight:enter("k")  --> "follower"  (waited for the leader: read the cache again)
--- flight:enter("k")  --> "alone"     (no lock: the wait timed out, or there is no dictionary. Ask PostgREST.)
--- ```
--- A follower takes the lock only to know that the leader finished. It frees the lock at once, so the
--- followers don't wait for each other.
---@param key string Cache key.
---@return "leader"|"follower"|"alone" role
function flight:enter(key)
	local settings = constants.locks
	local lock, new_error = self.lock_module:new(settings.dict, settings.options)
	if not lock then
		ngx.log(ngx.ERR, "Proxy lock is not available: ", new_error)
		return "alone"
	end

	local waited, lock_error = lock:lock(key)
	if not waited then
		if lock_error ~= "timeout" then ngx.log(ngx.ERR, "Proxy lock failed: ", lock_error) end
		return "alone"
	end

	if waited == 0 then
		self.held = lock
		return "leader"
	end

	lock:unlock()
	return "follower"
end

--- Free the lock of the leader. It does nothing for any other request.
---@return nil
function flight:leave()
	if self.held then
		self.held:unlock()
		self.held = nil
	end
end

return flight
