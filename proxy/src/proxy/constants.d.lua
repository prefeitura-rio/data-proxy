---@meta

--- All named constants of the proxy, in groups.
---@class Constants
---@field headers HeaderConstants Names of HTTP headers.
---@field http HttpConstants HTTP methods, statuses, media types and the 502 answer.
---@field disabled_endpoints string[] Path prefixes that are never cached, unless sync.json replaces them.
---@field policy_endpoints string[] Path prefixes of the access policy. A write there makes cached answers stale.
---@field log LogConstants Log messages and fields.
---@field redis RedisConstants Redis connection limits.
---@field postgrest PostgrestConstants Limits for the calls to PostgREST.
---@field jwt JwtConstants Reading the bearer token.
---@field routing RoutingConstants Choosing the PostgREST service.
---@field filter FilterConstants Reading the filter parameters of a query.
---@field config ConfigConstants Reading the config from the environment.

--- Names of HTTP headers.
---@class HeaderConstants
---@field forwarded string[] Request headers that are passed on to PostgREST. The proxy drops all others.
---@field response string[] PostgREST response headers that go to the client. The proxy drops all others.
---@field cached string[] Response headers stored together with a cached body.
---@field filter string Name of the header that carries the filter for PostgreSQL.

--- HTTP methods, statuses, media types and the 502 answer.
---@class HttpConstants
---@field read_methods table<string, boolean>
---@field body_methods table<string, boolean> Methods that must send a body, because resty.http refuses nil.
---@field retry_statuses table<integer, boolean> Statuses from PostgREST (502, 503, 504) after which an HA read retries.
---@field default_media_type string Media type assumed if the client sends no usable Accept.
---@field json_type string Content-Type written if PostgREST sent none.
---@field unavailable_body string Body of the 502 answer. The proxy sends it if no PostgREST service answers.
---@field unavailable_status integer

--- Log messages and fields.
---@class LogConstants
---@field messages table<string, string>
---@field fallback_message string Log message for an event that is not in messages.
---@field leading_fields string[] Fields that the log line shows before the duration, in this order.
---@field trailing_fields string[] Fields that the log line shows after the duration, in this order.

--- Redis connection limits.
---@class RedisConstants
---@field connect_timeout_ms integer
---@field keepalive_idle_ms integer
---@field keepalive_pool_size integer
---@field warning_interval_s integer Seconds between two warnings about a failed cache operation.
---@field generation_key string Redis key of the policy generation, a counter that is part of every cache key.

--- Limits for the calls to PostgREST.
---@class PostgrestConstants
---@field timeout_ms integer Time limit for one PostgREST request, in milliseconds. Queries can run long.

--- Values for reading the bearer token.
---@class JwtConstants
---@field anonymous_sub string

--- Rules that choose the PostgREST service.
---@class RoutingConstants
---@field rules table<string, table<string, RouteRule>> Rule by mode (single, ha) and request kind (read, write).

--- Values for reading the filter parameters of a query.
---@class FilterConstants
---@field group_keys table<string, boolean> Parameter keys that start a group: and, or, not.and, not.or.
---@field non_filter_keys table<string, boolean> Query keys that are not filters (PostgREST uses them for other tasks).
---@field scalar_operators string[] Operators that take one scalar value, longest name first.
---@field is_keywords string[]
---@field query_parameter_separator string

--- Values for reading the config from the environment.
---@class ConfigConstants
---@field env_prefix string Start of the name of every setting variable: cache_ttl is read from PROXY_CACHE_TTL.
---@field boolean_words table<string, boolean> Texts that a boolean setting accepts, in lower case, and their values.
