local helpers = require("tests.unit.helpers")
local log = require("proxy.log")

local check = helpers.check(describe, it)

check("log.format_note", function(case) return log.format_note(case.event, case.fields) end, {
	{
		name = "shows the message and the error",
		event = "sync-reload-failed",
		fields = { error = "boom" },
		expected = "Proxy sync.json reload failed: error=boom",
	},
	{
		name = "shows the operation before the error",
		event = "redis-failed",
		fields = { error = "timeout", operation = "read" },
		expected = "Proxy Redis cache failed: operation=read error=timeout",
	},
	{
		name = "shows the fallback message for an event that has no message",
		event = "unknown",
		fields = { error = "boom" },
		expected = "Proxy request state changed: error=boom",
	},
	{
		name = "skips a field that is not in the list of fields",
		event = "sync-load-failed",
		fields = { error = "boom", secret = "x" },
		expected = "Proxy sync.json load failed: error=boom",
	},
})
