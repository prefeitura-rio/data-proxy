---@diagnostic disable: missing-fields
local helpers = require("tests.unit.helpers")
local log = require("proxy.log")

local check = helpers.check(describe, it)

---@type RequestContext
local context = { method = "GET", uri = "/people", started = 100 }

check("log.render_fields", function(case) return log.render_fields(case.fields, case.names):join(" ") end, {
	{
		name = "writes the listed fields in the order of the names",
		fields = { bytes = 5, status = 200 },
		names = { "status", "bytes" },
		expected = "status=200 bytes=5",
	},
	{
		name = "skips a listed field that has no value",
		fields = { status = 200 },
		names = { "status", "source", "bytes" },
		expected = "status=200",
	},
	{
		name = "skips a field that no name lists",
		fields = { status = 200, extra = "x" },
		names = { "status" },
		expected = "status=200",
	},
	{ name = "writes nothing for an empty list of names", fields = { status = 200 }, names = {}, expected = "" },
})

check("log.format", function(case) return log.format(case.event, context, case.fields, case.now) end, {
	{
		name = "puts the duration between the leading and the trailing fields",
		event = "request",
		fields = { status = 200, source = "cache", bytes = 120, error = "none" },
		now = 100.003,
		expected = "Proxy request completed: method=GET path=/people status=200 source=cache"
			.. " duration_ms=3 bytes=120 error=none",
	},
	{
		name = "writes only the request and the duration for a call without fields",
		event = "request",
		fields = nil,
		now = 101,
		expected = "Proxy request completed: method=GET path=/people duration_ms=1000",
	},
	{
		name = "uses the message of the event",
		event = "lock-failed",
		fields = { error = "timeout" },
		now = 100,
		expected = "Proxy lock failed: method=GET path=/people duration_ms=0 error=timeout",
	},
	{
		name = "uses the fallback message for an unknown event",
		event = "unknown",
		fields = {},
		now = 100,
		expected = "Proxy request state changed: method=GET path=/people duration_ms=0",
	},
})

check("log.format_note", function(case) return log.format_note(case.event, case.fields) end, {
	{
		name = "writes the operation and the error",
		event = "redis-failed",
		fields = { operation = "read", error = "timeout" },
		expected = "Proxy Redis cache failed: operation=read error=timeout",
	},
	{
		name = "skips a field that the note doesn't list",
		event = "redis-failed",
		fields = { operation = "write", status = 500 },
		expected = "Proxy Redis cache failed: operation=write",
	},
	{
		name = "uses the fallback message for an unknown event",
		event = "unknown",
		fields = { error = "x" },
		expected = "Proxy request state changed: error=x",
	},
})
