---@meta

--- The PostgREST services for one kind of request: the first one and the retry.
---@class RouteRule
---@field primary "read"|"write" The PostgREST service that gets the request first.
---@field retry "read"|"write"|nil The service that gets the retries. nil means no retry.

--- The PostgREST services that one request uses.
---@class Route
---@field postgrest string Base URL of the PostgREST service that gets the request first.
---@field retry_postgrest string|nil Base URL for the retries. Set only for reads in HA mode.
