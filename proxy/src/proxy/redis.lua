-- Read and write cached answers in Redis, with one connection for each call.

local cjson = require("cjson.safe")
local class = require("pl.class") --[[@as pl.ClassModule]]
local constants = require("proxy.constants")
local log = require("proxy.log")
local resty_redis = require("resty.redis")
local schemas = require("proxy.schemas.main")

local last_warning = -math.huge

--- Log a warning about a failed cache operation, at most once in each warning interval of a worker.
--- The cache is optional, so a failure does not fail the request. The warning is the only sign of it.
---@param operation string What failed: connection, read, or write.
---@param reason any The error from the Redis client.
local function warn(operation, reason)
	local now = ngx.now()
	if now - last_warning < constants.redis.warning_interval_s then return end

	last_warning = now
	log.note(ngx.WARN, "redis-failed", { operation = operation, error = tostring(reason) })
end

--- The Redis cache store, with one connection for each call.
---@class Redis
local Redis = class() --[[@as Redis]]

--- Make a store that connects to the node in the config.
---@param config Config
function Redis:_init(config) self.config = config end

--- Open a connection to the Redis node, log in, and select the database.
--- A connection from the keepalive pool is already logged in and on the database, so it skips both.
--- The host, the port, the password and the database are in the config (redis_*).
---@return table|nil connection The connection. nil if the connection, the login, or the database selection failed.
---@return string|nil error Reason for the failure.
function Redis:open()
	local connection = resty_redis:new() --[[@as table]]
	connection:set_timeout(constants.redis.connect_timeout_ms)

	local connected, connect_error = connection:connect(self.config.redis_host, self.config.redis_port)
	if not connected then return nil, connect_error end
	if (connection:get_reused_times() or 0) > 0 then return connection end

	local password = self.config.redis_password
	if password and password ~= "" then
		local authenticated, auth_error = connection:auth(password)
		if not authenticated then return nil, auth_error end
	end
	local selected, select_error = connection:select(self.config.redis_database)
	if not selected then return nil, select_error end
	return connection
end

--- Give a connection back to the keepalive pool.
---@param connection table An open connection from Redis:open.
function Redis:release(connection)
	connection:set_keepalive(constants.redis.keepalive_idle_ms, constants.redis.keepalive_pool_size)
end

--- Read one cached entry.
---@param key string Cache key.
---@return CachedEntry|nil entry nil if the key is missing, the connection failed, or the entry is not valid.
function Redis:read(key)
	local connection, open_error = self:open()
	if not connection then
		warn("connection", open_error)
		return nil
	end

	local raw, read_error = connection:get(key)
	self:release(connection)
	if read_error then
		warn("read", read_error)
		return nil
	end
	if not raw or raw == ngx.null then return nil end

	return (schemas.cached_entry:validate(cjson.decode(raw)))
end

--- Read the policy generation: a counter that grows each time the access policy changes.
--- Every cache key includes it, so a change makes all earlier entries unreachable.
---@return integer generation 0 if no change was counted, or if Redis does not answer.
function Redis:generation()
	local connection, open_error = self:open()
	if not connection then
		warn("connection", open_error)
		return 0
	end

	local raw, read_error = connection:get(constants.redis.generation_key)
	self:release(connection)
	if read_error then
		warn("read", read_error)
		return 0
	end

	return tonumber(raw) or 0
end

--- Count one change of the access policy.
---@return boolean counted false if Redis did not answer.
function Redis:bump_generation()
	local connection, open_error = self:open()
	if not connection then
		warn("connection", open_error)
		return false
	end

	local count, incr_error = connection:incr(constants.redis.generation_key)
	self:release(connection)
	if not count then
		warn("write", incr_error)
		return false
	end

	return true
end

--- Store one entry. The entry expires after the TTL.
---@param key string Cache key.
---@param ttl string|number Lifetime in seconds.
---@param value CachedEntry The body and headers to store.
---@return boolean stored false if the connection failed or Redis rejected the write.
function Redis:write(key, ttl, value)
	local connection, open_error = self:open()
	if not connection then
		warn("connection", open_error)
		return false
	end

	local encoded = cjson.encode(value)
	local reply, write_error = connection:setex(key, ttl, encoded)
	self:release(connection)
	if reply ~= "OK" and reply ~= true then
		warn("write", write_error or reply)
		return false
	end
	return true
end

return Redis
