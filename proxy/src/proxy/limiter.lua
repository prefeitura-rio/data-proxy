-- Limit the requests that wait for PostgREST, so that a full database answers fast instead of queueing.

local constants = require("proxy.constants")

--- The functions that count and limit the requests that wait for PostgREST.
---@class Limiter
local limiter = {}

--- Run a task if fewer than limit tasks run, and free the slot afterwards, also if the task fails.
--- The count is in a shared dictionary, so all the nginx workers of the pod add to the same number.
---
--- ```lua
--- limiter.run(dict, 2, function() return "answer" end)  --> "answer"
--- limiter.run(dict, 2, task)                           --> nil, "overloaded"  (two tasks already run)
--- ```
---@generic A, B
---@param dict table|nil The shared dictionary with an incr method. nil means no limit.
---@param limit integer Most tasks that run at the same time. 0 means no limit.
---@param task fun(): A, B? The work, for example the call to PostgREST.
---@return A|nil first The first answer of the task, or nil if the limiter rejected it.
---@return B|string|nil second The second answer of the task, or the reason of the rejection.
function limiter.run(dict, limit, task)
	if limit == 0 or dict == nil then return task() end

	local key = constants.limiter.key
	local count = dict:incr(key, 1, 0)
	if count and count > limit then
		dict:incr(key, -1)
		return nil, constants.http.overloaded_reason
	end

	local ok, first, second = pcall(task)
	if count then dict:incr(key, -1) end
	if not ok then error(first, 0) end

	return first, second
end

return limiter
