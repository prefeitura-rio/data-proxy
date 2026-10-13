-- Send the answer to the client and log it.

local constants = require("proxy.constants")
local log = require("proxy.log")
local tablex = require("pl.tablex") --[[@as pl.tablex]]

--- Functions that send the answer to the client.
---@class Responder
local response = {}

--- Decide the answer to send. The function writes nothing and changes nothing.
---
--- ```lua
--- response.plan({ response = nil, source = "none" })
--- --> { status = 502, body = '{"error":"PostgREST unavailable"}', headers = {}, log_fields = { status = 502, ... } }
--- ```
---@param result CacheResult The result of cache.fetch.
---@return ResponsePlan plan
function response.plan(result)
	local answer = result.response
	if not answer then
		return {
			status = constants.http.unavailable_status,
			body = constants.http.unavailable_body,
			headers = {},
			log_fields = {
				status = constants.http.unavailable_status,
				source = "none",
				bytes = #constants.http.unavailable_body,
			},
		}
	end

	return {
		status = answer.status,
		body = answer.body,
		headers = answer.headers,
		x_cache = result.source == "cache" and "HIT" or "MISS",
		default_content_type = constants.http.json_type,
		log_fields = {
			status = answer.status,
			source = answer.headers["X-Source"] or result.source,
			bytes = #answer.body,
		},
	}
end

--- Send the answer of a request to the client and log it.
---@param request NgxRequest The nginx request. The function uses it to set headers and send the answer.
---@param context RequestContext
---@param result CacheResult The result of cache.fetch.
---@return nil
function response.send(request, context, result)
	local plan = response.plan(result)

	tablex.update(request.headers_out, plan.headers)
	if plan.default_content_type then
		request.headers_out["Content-Type"] = request.headers_out["Content-Type"] or plan.default_content_type
	end
	if plan.x_cache then request.headers_out["X-Cache"] = plan.x_cache end

	log.write(ngx.INFO, "request", context, plan.log_fields)
	request:respond(plan.status, plan.body)
end

return response
