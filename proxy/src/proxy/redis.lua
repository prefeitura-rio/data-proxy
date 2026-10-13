-- Read and write cached answers in Redis, with one connection for each call.

local cjson = require("cjson.safe")
local class = require("pl.class") --[[@as pl.ClassModule]]
local constants = require("proxy.constants")
local resty_redis = require("resty.redis")
local schemas = require("proxy.schemas.main")

--- The Redis cache store, with one connection for each call.
---@class Redis
local Redis = class() --[[@as Redis]]

--- Make a store that connects to the node in the config.
---@param config Config
function Redis:_init(config) self.config = config end

--- Open a connection to the Redis node, log in, and select the database.
--- A connection from the keepalive pool is already logged in and on the database, so it skips both.
--- The host, the port, the password and the database are in the config (redis_*).
---@return table|nil connection The connection. nil if the connection or the login failed.
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
	connection:select(self.config.redis_database)
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
	local connection = self:open()
	if not connection then return nil end

	local raw = connection:get(key)
	self:release(connection)
	if not raw or raw == ngx.null then return nil end

	return (schemas.cached_entry:validate(cjson.decode(raw)))
end

--- Store one entry. The entry expires after the TTL.
---@param key string Cache key.
---@param ttl string|number Lifetime in seconds.
---@param value CachedEntry The body and headers to store.
---@return boolean stored false if the connection failed or Redis rejected the write.
function Redis:write(key, ttl, value)
	local connection = self:open()
	if not connection then return false end

	local encoded = cjson.encode(value)
	local reply = connection:setex(key, ttl, encoded)
	self:release(connection)
	return reply == "OK" or reply == true
end

return Redis
