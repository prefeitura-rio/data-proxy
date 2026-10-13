---@diagnostic disable: missing-fields

local helpers = require("tests.unit.helpers")
local routing = require("proxy.routing")

local check = helpers.check(describe, it)

check(
	"routing.select",
	function(case)
		return routing.select(
			case.method,
			{ ha_mode = case.ha_mode, postgrest_read = "read", postgrest_write = "write" }
		)
	end,
	{
		{
			name = "single mode reads and writes through the read endpoint",
			method = "POST",
			ha_mode = false,
			expected = { postgrest = "read", retry_postgrest = nil },
		},
		{
			name = "HA reads retry on the write endpoint",
			method = "GET",
			ha_mode = true,
			expected = { postgrest = "read", retry_postgrest = "write" },
		},
		{
			name = "HA writes use the write endpoint",
			method = "PATCH",
			ha_mode = true,
			expected = { postgrest = "write", retry_postgrest = nil },
		},
		{
			name = "HA reads a HEAD request from the read endpoint",
			method = "HEAD",
			ha_mode = true,
			expected = { postgrest = "read", retry_postgrest = "write" },
		},
		{
			name = "HA sends an unknown method to the write endpoint",
			method = "BREW",
			ha_mode = true,
			expected = { postgrest = "write", retry_postgrest = nil },
		},
	}
)
