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
		unavailable_status = 502,
	},
	disabled_endpoints = { "/access_policy" },
	policy_endpoints = { "/access_policy" },
	log = {
		messages = {
			request = "Proxy request completed",
			["postgrest-retried"] = "Proxy PostgREST request retry started",
		},
		fallback_message = "Proxy request state changed",
		leading_fields = { "status", "source" },
		trailing_fields = { "bytes", "error" },
	},
	redis = {
		connect_timeout_ms = 1000,
		keepalive_idle_ms = 60000,
		keepalive_pool_size = 32,
		warning_interval_s = 30,
		generation_key = "policy:generation",
	},
	postgrest = {
		timeout_ms = 120000,
	},
	jwt = {
		anonymous_sub = "anon",
	},
	routing = {
		rules = {
			single = {
				read = { primary = "read", retry = nil },
				write = { primary = "read", retry = nil },
			},
			ha = {
				read = { primary = "read", retry = "write" },
				write = { primary = "write", retry = nil },
			},
		},
	},
	filter = {
		group_keys = {
			["and"] = true,
			["or"] = true,
			["not.and"] = true,
			["not.or"] = true,
		},
		non_filter_keys = { select = true, fields = true },
		scalar_operators = { "neq", "eq", "gte", "gt", "lte", "lt", "ilike", "like" },
		is_keywords = { "null", "not_null", "true", "false", "unknown" },
		query_parameter_separator = "&",
	},
	config = {
		env_prefix = "PROXY_",
		boolean_words = { ["true"] = true, ["1"] = true, ["false"] = false, ["0"] = false },
	},
}
