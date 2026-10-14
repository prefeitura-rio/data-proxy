---@meta

--- The config of the proxy, read from environment variables.
---@class Config
---@field cache_ttl integer Default cache lifetime in seconds. A table in sync.json can set its own.
---@field max_body integer Largest cacheable body in bytes. 0 means no limit.
---@field redis_host string Host name of the Redis node for the cache.
---@field redis_port integer Port of the Redis node for the cache.
---@field redis_database integer Redis database number for cached responses.
---@field redis_password string|nil Redis password. nil means no login.
---@field postgrest string Base URL of the PostgREST service.
---@field max_inflight integer Most requests that wait for PostgREST at the same time in one proxy. 0 means no limit.
---@field config_url string|nil Address where the console serves sync.json. nil means the proxy reads the file only.
---@field config_refresh_seconds integer Seconds between two checks of the console for a new sync.json.

--- The schema of the config. It reads the text of the variables.
---@class SchemasModule
---@field config Schema<Config>
