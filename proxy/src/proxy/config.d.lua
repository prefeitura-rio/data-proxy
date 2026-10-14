---@meta

--- The config of the proxy, read from environment variables.
---@class Config
---@field cache_ttl integer Default cache lifetime in seconds. A table in sync.json can set its own.
---@field max_body integer Largest cacheable body in bytes. 0 means no limit.
---@field ha_mode boolean True if the proxy runs in HA mode: reads can retry on the write service.
---@field redis_host string Host name of the Redis node for the cache.
---@field redis_port integer Port of the Redis node for the cache.
---@field redis_database integer Redis database number for cached responses.
---@field redis_password string|nil Redis password. nil means no login.
---@field postgrest_read string Base URL of the PostgREST service for reads.
---@field postgrest_write string Base URL of the PostgREST service for writes.
---@field postgrest_retries integer Number of retry attempts on the retry service after the first attempt.
---@field postgrest_backoff_ms integer Delay in milliseconds before the first retry. It doubles each retry.
---@field max_inflight integer Most requests that wait for PostgREST at the same time in one proxy. 0 means no limit.

--- The schema of the config. It reads the text of the variables.
---@class SchemasModule
---@field config Schema<Config>
