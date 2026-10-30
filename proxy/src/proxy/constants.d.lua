---@meta

--- All named constants of the proxy, in groups.
---@class Constants
---@field headers HeaderConstants Names of HTTP headers.
---@field limiter LimiterConstants Counter of the requests that wait for PostgREST.
---@field locks LockConstants The lock that lets one request ask PostgREST for a cache key.
---@field http HttpConstants HTTP methods, statuses, media types and the 502 answer.
---@field internal_profile string The schema profile that only the cluster may use. The proxy refuses it.
---@field disabled_endpoints string[] Path prefixes that are never cached, unless sync.json replaces them.
---@field policy_endpoints string[] Path prefixes of the access policy. A write there makes cached answers stale.
---@field log LogConstants Log messages and fields.
---@field redis RedisConstants Redis connection limits.
---@field postgrest PostgrestConstants Limits for the calls to PostgREST.
---@field jwt JwtConstants Reading the bearer token.
---@field time TimeConstants Units of time.
---@field settings SettingsConstants Limits for the call to the console that gives sync.json.
---@field filter FilterConstants Reading the filter parameters of a query.
---@field aggregate AggregateConstants Calling the aggregate function of a table.
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
---@field retry_statuses table<integer, boolean> Statuses (502, 503, 504) after which a read is sent again.
---@field default_media_type string Media type assumed if the client sends no usable Accept.
---@field json_type string Content-Type written if PostgREST sent none.
---@field unavailable_body string Body of the 502 answer. The proxy sends it if no PostgREST service answers.
---@field reload_failed_body string JSON body of the answer if the reload fails.
---@field unavailable_status integer
---@field overloaded_body string Body of the 503 answer. The proxy sends it if too many requests wait for PostgREST.
---@field forbidden_body string Body of the 403 answer, for a request that asks for the console profile.
---@field overloaded_status integer
---@field overloaded_reason string The reason that limiter.run gives if it rejects a task.
---@field retry_after_seconds string Value of the Retry-After header of the 503 answer.
---@field ok_status integer The status of a good answer.
---@field answer_statuses table<integer, boolean> The statuses of an aggregate answer: 200, and 206 for the cap.
---@field success_from integer The first status of the success range.
---@field success_before integer The first status above the success range.

--- The lock that lets one request ask PostgREST for a cache key, while the other requests for it wait.
---@class LockConstants
---@field dict string Name of the shared dictionary that holds the locks.
---@field options LockOptions The options of resty.lock.

--- The options that resty.lock takes.
---@class LockOptions
---@field exptime number Seconds after which a lock expires.
---@field timeout number Most seconds that a request waits for the lock.
---@field step number Seconds of the first wait between two tries.
---@field ratio number The factor that grows the wait.
---@field max_step number The longest wait between two tries.

--- The shared counter of the requests that wait for PostgREST.
---@class LimiterConstants
---@field dict string Name of the lua_shared_dict in nginx.conf.
---@field key string Key of the counter in that dictionary.

--- Log messages and fields.
---@class LogConstants
---@field messages table<string, string>
---@field fallback_message string Log message for an event that is not in messages.
---@field leading_fields string[] Fields that the log line shows before the duration, in this order.
---@field trailing_fields string[] Fields that the log line shows after the duration, in this order.
---@field note_fields string[] Fields that a line without a request shows, in this order.

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
---@field retries integer How many times a read that failed is sent again.
---@field backoff_ms integer Wait before the first retry, in milliseconds. It grows by backoff_factor for each retry.
---@field backoff_factor integer How many times longer each retry waits than the one before.

--- Units of time.
---@class TimeConstants
---@field milliseconds_per_second integer

--- Limits for the call to the console that gives sync.json, in milliseconds.
---@class SettingsConstants
---@field connect_ms integer
---@field send_ms integer
---@field read_ms integer

--- Values for reading the bearer token.
---@class JwtConstants
---@field anonymous_sub string

--- Values for reading the filter parameters of a query.
---@class FilterConstants
---@field group_keys table<string, boolean> Parameter keys that start a group: and, or, not.and, not.or.
---@field non_filter_keys table<string, boolean> Query keys that are not filters (PostgREST uses them for other tasks).
---@field reserved_keys table<string, boolean> The query keys that the parser pushes: select, order, limit, and offset.
---@field max_limit_digits integer Most digits of a limit or an offset that the parser pushes.
---@field order_modifiers table<string, table> The fields that each word after a column in an order item sets.
---@field scalar_operators string[] Operators that take one scalar value, longest name first.
---@field aggregate_functions string[] The aggregate functions that the parser pushes to DuckDB.
---@field is_keywords string[]
---@field query_parameter_separator string
---@field list_separator string Separates the items of a list in a value, for example in select and order.
---@field part_separator string Separates the parts of a value, for example id.desc, and the key from the value.
---@field first_modifier_index integer The position of the first modifier in an order item.
---@field percent_escape_digits integer How many hexadecimal digits follow a percent sign in an escape.

--- Values for calling the aggregate function that runs a group by in DuckDB.
---@class AggregateConstants
---@field rpc_prefix string Path of the PostgREST functions.
---@field function_suffix string Added to the table name to make the name of the function.
---@field max_function_bytes integer The longest name of a PostgreSQL function.
---@field empty_body string The body that tells the proxy to send the original request.

--- Values for reading the config from the environment.
---@class ConfigConstants
---@field env_prefix string Start of the name of every setting variable: cache_ttl is read from PROXY_CACHE_TTL.
