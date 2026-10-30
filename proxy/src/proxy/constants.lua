-- Every named constant of the proxy, in groups. The Constants type in constants.d.lua describes them.

---@type Constants
return {
	headers = {
		forwarded = {
			"Authorization",
			"Accept-Profile",
			"Content-Profile",
			"Prefer",
			"Range",
			"Accept",
			"Content-Type",
			"X-DuckLake-Snapshot",
		},
		response = {
			"Content-Type",
			"Content-Range",
			"Location",
			"Preference-Applied",
			"WWW-Authenticate",
			"X-Source",
			"X-DuckLake-Snapshot",
		},
		cached = { "X-Source", "X-DuckLake-Snapshot" },
		filter = "X-DuckLake-Filter",
	},
	http = {
		read_methods = { GET = true, HEAD = true },
		body_methods = { POST = true, PUT = true, PATCH = true },
		retry_statuses = { [502] = true, [503] = true, [504] = true },
		default_media_type = "application/json",
		json_type = "application/json; charset=utf-8",
		unavailable_body = '{"error":"PostgREST unavailable"}',
		reload_failed_body = '{"error":"Could not reload sync.json"}',
		unavailable_status = 502,
		overloaded_body = '{"error":"Proxy overloaded"}',
		forbidden_body = '{"error":"The console schema is not available through the proxy"}',
		overloaded_status = 503,
		overloaded_reason = "overloaded",
		retry_after_seconds = "1",
		ok_status = 200,
		--- The statuses of an answer of the aggregate function. 206 is the cap of the function.
		answer_statuses = { [200] = true, [206] = true },
		success_from = 200,
		success_before = 300,
	},
	limiter = {
		dict = "proxy_inflight",
		key = "inflight",
	},
	locks = {
		dict = "proxy_locks",
		options = {
			exptime = 60, -- A leader that dies frees the key after this many seconds.
			timeout = 30, -- A follower waits this long for the leader, and then asks PostgREST itself.
			step = 0.001,
			ratio = 2,
			max_step = 0.05, -- A follower looks at the lock at least every 50 ms, so it wakes up soon after the leader.
		},
	},
	internal_profile = "console",
	disabled_endpoints = { "/access_policy" },
	policy_endpoints = { "/access_policy" },
	log = {
		messages = {
			request = "Proxy request completed",
			["postgrest-retried"] = "Proxy PostgREST request retry started",
			["aggregate-failed"] = "Proxy aggregate function failed, sent the original request",
			["lock-unavailable"] = "Proxy lock is not available",
			["lock-failed"] = "Proxy lock failed",
			["redis-failed"] = "Proxy Redis cache failed",
			["sync-load-failed"] = "Proxy sync.json load failed",
			["sync-reload-failed"] = "Proxy sync.json reload failed",
		},
		fallback_message = "Proxy request state changed",
		leading_fields = { "status", "source" },
		trailing_fields = { "bytes", "error" },
		note_fields = { "operation", "error" },
	},
	redis = {
		connect_timeout_ms = 1000,
		keepalive_idle_ms = 60000,
		keepalive_pool_size = 32,
		warning_interval_s = 30,
		generation_key = "policy:generation",
	},
	postgrest = {
		timeout_ms = 30000,
		retries = 2,
		backoff_ms = 500,
		backoff_factor = 2,
	},
	jwt = {
		anonymous_sub = "anon",
	},
	time = {
		milliseconds_per_second = 1000,
	},
	settings = {
		connect_ms = 2000,
		send_ms = 5000,
		read_ms = 5000,
	},
	filter = {
		group_keys = {
			["and"] = true,
			["or"] = true,
			["not.and"] = true,
			["not.or"] = true,
		},
		non_filter_keys = { fields = true },
		reserved_keys = { select = true, order = true, limit = true, offset = true },
		max_limit_digits = 9,
		order_modifiers = {
			asc = { descending = false },
			desc = { descending = true },
			nullsfirst = { nulls = "first" },
			nullslast = { nulls = "last" },
		},
		scalar_operators = { "neq", "eq", "gte", "gt", "lte", "lt", "ilike", "like" },
		aggregate_functions = { "sum", "avg", "count", "min", "max" },
		is_keywords = { "null", "not_null", "true", "false", "unknown" },
		query_parameter_separator = "&",
		list_separator = ",",
		part_separator = ".",
		first_modifier_index = 2,
		percent_escape_digits = 2,
	},
	aggregate = {
		rpc_prefix = "/rpc/",
		function_suffix = "_aggregate",
		max_function_bytes = 63, -- PostgreSQL cuts a longer identifier without an error.
		empty_body = "null", -- The answer of the function for a request that the table function must serve.
	},
	config = {
		env_prefix = "PROXY_",
	},
}
