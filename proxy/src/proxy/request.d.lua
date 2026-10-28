---@meta

--- The values that request.make_context needs besides the request: the table settings and the time.
---@class RequestDeps
---@field config SyncConfig The table settings that main.init loaded.
---@field now number The current time, in seconds, with fractions.

--- The values that decide the cache key of a request.
---@class CacheKeyParts
---@field method string HTTP method.
---@field uri string Request path.
---@field args string Raw query string, empty if absent.
---@field sub string User name taken from the JWT, or anon.
---@field schemas string Comma-separated schemas claim of the JWT. Empty if absent.
---@field role string Role claim of the JWT. Empty if absent.
---@field profile string Value of the Accept-Profile header. Empty if absent.
---@field generation integer Policy generation. A change of the access policy gives every cached answer a new key.
---@field revision integer|nil Revision of the access policy and the rules. A change gives every answer a new key.
---@field headers table<string,string|nil> Request headers that are forwarded to PostgREST.

--- A request with all the values that the proxy needs. It does not depend on nginx.
---@class RequestContext: CacheKeyParts
---@field postgrest string Base URL of the PostgREST service.
---@field cache_ttl string Cache lifetime in seconds. Empty if the request must not be cached.
---@field max_body number Largest cacheable body in bytes. 0 means no limit.
---@field body string Request body. Empty if absent.
---@field disabled_endpoints string[] Path prefixes that are never cached.
---@field aggregate_path string|nil Path of the function that runs the aggregate of the request in DuckDB.
---@field started number Time at which the proxy started the request (seconds, with fractions).

--- The claims of a JWT that the proxy reads. Other claims are ignored.
---@class JwtClaims
---@field sub string|nil Subject of the token.
---@field preferred_username string|nil User name. It is used before sub.
---@field schemas string|string[]|nil The schemas of the user, as a text or a list.
---@field role string|nil The role of the user, one role of rbac.toml.

--- The user, the schemas, and the role from a JWT.
---@class JwtIdentity
---@field sub string User name. The value is preferred_username, or sub, or anon.
---@field schemas string The schemas claim, separated by commas. Empty if the token has no schemas.
---@field role string The role claim. Empty if the token has none.

--- The schema of the payload of a bearer token.
---@class SchemasModule
---@field jwt_claims Schema<JwtClaims>
