-- Format and write one log line for each request event: a message, then name=value pairs.

local List = require("pl.List") --[[@as pl.ListClass]]
local constants = require("proxy.constants")

--- Functions that format and write log lines.
---@class Log
local log = {}

--- Render each listed field that has a value as one name=value text, in the order of the names.
---
--- ```lua
--- log.render_fields({ status = 200, bytes = 5 }, { "status", "source", "bytes" })  --> { "status=200", "bytes=5" }
--- ```
---@param fields table<string, string|number|nil> Field values.
---@param names string[] Names of the fields to show, in order.
---@return pl.List<string> texts
function log.render_fields(fields, names)
	return List(names)
		:filter(function(name) return fields[name] ~= nil end)
		:map(function(name) return name .. "=" .. fields[name] end)
end

--- Build one log line.
---
--- ```lua
--- log.format("request", context, { status = 200, source = "cache", bytes = 120 })
--- --> "Proxy request completed: method=GET path=/people status=200 source=cache duration_ms=3 bytes=120"
--- ```
---@param event string Event name. It is a key of constants.log.messages, for example request.
---@param context RequestContext The request context. The function uses method, uri, and started.
---@param fields table<string, string|number|nil>|nil Extra values: status, source, bytes, error.
---@param now number The current time, in seconds. The duration is now minus context.started.
---@return string line For example Proxy request completed: method=GET path=/people duration_ms=3.
function log.format(event, context, fields, now)
	fields = fields or {}
	local elapsed_ms = math.floor((now - context.started) * constants.time.milliseconds_per_second)

	local texts = List({ "method=" .. context.method, "path=" .. context.uri })
		:extend(log.render_fields(fields, constants.log.leading_fields))
		:append("duration_ms=" .. elapsed_ms)
		:extend(log.render_fields(fields, constants.log.trailing_fields))

	local message = constants.log.messages[event] or constants.log.fallback_message
	return message .. ": " .. texts:join(" ")
end

--- Write one log line to the nginx error log.
---@param level integer nginx log level, for example ngx.INFO or ngx.WARN.
---@param event string Event name, see log.format.
---@param context RequestContext The request context.
---@param fields table<string, string|number|nil>|nil Extra values, see log.format.
---@return nil
function log.write(level, event, context, fields)
	ngx.log(level --[[@as ngx.log.level]], log.format(event, context, fields, ngx.now()))
end

return log
