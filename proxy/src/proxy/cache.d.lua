---@meta

--- The answer that the cache stores: the body and the headers to keep.
---@class CachedEntry
---@field body string Response body that was stored.
---@field headers HeaderMap The stored response headers (X-Source, X-DuckLake-Snapshot).

--- A store that reads and writes cached answers. Tests use a fake store.
---@class CacheStore
---@field read fun(self: CacheStore, key: string): CachedEntry|nil Finds one stored entry.
---@field write fun(self: CacheStore, key: string, ttl: string|number, value: CachedEntry): boolean Stores one entry.

--- A lock of one cache key, from resty.lock. Tests use a fake lock.
---@class CacheLock
---@field lock fun(self: CacheLock, key: string): number|nil, string|nil Takes the lock. Gives the wait in seconds.
---@field unlock fun(self: CacheLock): boolean|nil, string|nil Frees the lock.

--- The resty.lock module, as the cache uses it. Tests use a fake module.
---@class CacheLocks
---@field new fun(self: CacheLocks, dict: string, options: LockOptions): CacheLock|nil, string|nil Makes a lock.

--- The result of cache.fetch: the response and where it came from.
---@class CacheResult
---@field response Response|nil The response to send. nil if PostgREST failed.
---@field source string Where the response came from: cache, postgrest or none.
---@field error string|nil Why PostgREST failed. Present only if response is nil.
---@field leading boolean Always true today. Reserved for sharing the answer of the leader with the requests that wait.

--- The schema of the entry that the cache stores in Redis.
---@class SchemasModule
---@field cached_entry Schema<CachedEntry>
