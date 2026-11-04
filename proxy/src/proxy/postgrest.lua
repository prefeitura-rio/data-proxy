-- Send requests to PostgREST. A read that fails is sent again, with a backoff between the attempts.

local cjson = require("cjson.safe")
local constants = require("proxy.constants")
local http = require("resty.http")
local log = require("proxy.log")
local stringx = require("pl.stringx") --[[@as pl.stringx]]
local tablex = require("pl.tablex") --[[@as pl.tablex]]
local utils = require("proxy.utils")

--- Functions that send requests to PostgREST.
---@class Postgrest
local postgrest = {}

--- Make the URL of one PostgREST request: the base URL, the encoded path, and the query string.
---
--- ```lua
--- request_url("http://r", { uri = "/my table", args = "id=eq.1" })  --> "http://r/my%20table?id=eq.1"
--- ```
---@param target string Base URL of the PostgREST service.
---@param context RequestContext
---@return string url
local function request_url(target, context)
	local path = utils.encode_path(context.uri)
	local query = context.args ~= "" and "?" .. context.args or ""
	return target .. path .. query
end

--- Send one request to one PostgREST service.
---@param context RequestContext
---@param target string Base URL of the PostgREST service.
---@return Response|nil response nil if the service is not reachable.
---@return string|nil error Reason for the failure.
local function send(context, target)
	local client, client_error = http.new()
	if not client then return nil, client_error end
	client:set_timeout(constants.postgrest.timeout_ms)

	local http_response, request_error = client:request_uri(request_url(target, context), {
		method = context.method,
		headers = context.headers,
		body = context.body ~= "" and context.body or (constants.http.body_methods[context.method] and "" or nil),
		keepalive = true,
	})

	if not http_response then return nil, request_error end

	return {
		status = http_response.status,
		body = http_response.body or "",
		headers = postgrest.forwarded_headers(http_response.headers),
	}
end

--- Pick the response headers that the proxy forwards to the client: only the names in constants.headers.response.
--- A header that is not in the list, or that the response does not have, is left out.
---
--- ```lua
--- postgrest.forwarded_headers({ ["X-Source"] = "ducklake", ["Set-Cookie"] = "a=1" })
--- --> { ["X-Source"] = "ducklake" }
--- ```
---@param headers HeaderMap Headers of the PostgREST response.
---@return HeaderMap headers
function postgrest.forwarded_headers(headers) return utils.pick(headers, constants.headers.response) end

--- Decide if the last attempt must be tried again.
--- The answer is yes only for a read that failed, or that returned a status in constants.http.retry_statuses
--- (502, 503, 504). A write is never sent twice, because it could change the data twice.
---
--- ```lua
--- postgrest.should_retry({ method = "GET" }, { status = 503 })   --> true
--- postgrest.should_retry({ method = "GET" }, { status = 200 })   --> false
--- postgrest.should_retry({ method = "POST" }, { status = 503 })  --> false
--- ```
---@param context RequestContext
---@param response Response|nil The result of the last attempt. nil if the attempt failed.
---@return boolean retry
function postgrest.should_retry(context, response)
	if not constants.http.read_methods[context.method] then return false end
	return response == nil or constants.http.retry_statuses[response.status] == true
end

--- Ask the aggregate function of the table, which runs the group by in DuckDB.
--- The function answers null for a request that the table function must serve, for example one that reads
--- a source other than DuckLake. The proxy then sends the original request. It also sends it after any
--- other failure, so the function never makes a request fail that the table would answer.
---@param context RequestContext
---@param sender Sender
---@return Response|nil response nil if the table must answer.
local function ask_aggregate(context, sender)
	if context.aggregate_path == nil then return nil end

	local aggregate = tablex.copy(context)
	aggregate.uri, aggregate.args = context.aggregate_path, ""

	local response = sender(aggregate, context.postgrest)
	if response and constants.http.answer_statuses[response.status] then
		if stringx.strip(response.body) ~= constants.aggregate.empty_body then return response end
		return nil
	end

	log.write(ngx.INFO, "aggregate-failed", context, { status = response and response.status })
	return nil
end

--- Tell whether a value is a number of rows: a whole number that is not negative.
---@param value any
---@return boolean
local function is_count(value) return type(value) == "number" and value >= 0 and value == math.floor(value) end

--- Read the number of rows from the answer of the count function.
---@param response Response|nil
---@return integer|nil total nil if the function did not count, for example for a table with a fallback source.
---@return string|nil snapshot The DuckLake snapshot that the count read.
local function read_total(response)
	if not response or response.status ~= constants.http.ok_status then return nil end

	local rows = cjson.decode(response.body)
	local total = type(rows) == "table" and type(rows[1]) == "table" and rows[1][constants.count.alias] or nil
	local snapshot = response.headers["X-DuckLake-Snapshot"]
	if snapshot and is_count(total) then return total, snapshot end
	return nil
end

--- Count the rows of a read in DuckDB, with the aggregate function of the table.
--- The proxy sends the original request if the function does not count or fails, so the count never makes a
--- request fail that PostgREST would answer.
---@param context RequestContext
---@param sender Sender
---@return integer|nil total
---@return string|nil snapshot The DuckLake snapshot that the count read.
local function ask_count(context, sender)
	if context.count == nil then return nil end

	local count = tablex.copy(context)
	count.uri, count.args, count.headers = context.count.path, "", context.count.count_headers

	local response = sender(count, context.postgrest)
	local total, snapshot = read_total(response)
	if total then return total, snapshot end

	log.write(ngx.INFO, "count-failed", context, { status = response and response.status })
	return nil
end

--- Add the total to the page that PostgREST did not count: the end of the Content-Range, the status, and the
--- preference that the proxy applied. The status is 206 unless the page holds every row, as PostgREST does.
---@param page Response The answer of PostgREST to the page.
---@param total integer The number of rows in DuckDB.
---@return Response|nil answer nil if the page is empty or does not match the total.
local function add_total(page, total)
	local range = page.headers["Content-Range"] or ""
	local first, last = range:match("^(%d+)%-(%d+)/%*$")
	first, last = tonumber(first), tonumber(last)
	if first == nil or last == nil or last >= total then return nil end

	local headers = tablex.copy(page.headers)
	headers["Content-Range"] = ("%d-%d/%d"):format(first, last, total)
	headers["Preference-Applied"] = constants.count.prefer
	local complete = first == 0 and last == total - 1
	return {
		status = complete and constants.http.ok_status or constants.http.partial_status,
		body = page.body,
		headers = headers,
	}
end

--- Send a request to PostgREST, and send a read again while the answer is retryable.
---@param context RequestContext
---@param sender Sender
---@param sleeper Sleeper
---@return Response|nil response nil if all attempts fail.
---@return string|nil error The reason for the last failure.
local function send_with_retries(context, sender, sleeper)
	local response, response_error = sender(context, context.postgrest)
	for attempt = 1, constants.postgrest.retries do
		if not postgrest.should_retry(context, response) then break end
		log.write(ngx.WARN, "postgrest-retried", context, { status = response and response.status })
		sleeper(constants.postgrest.backoff_ms * constants.postgrest.backoff_factor ^ (attempt - 1))
		response, response_error = sender(context, context.postgrest)
	end

	return response, response_error
end

--- Answer a read with `Prefer: count=exact`: count in DuckDB, then read the page at the snapshot of the count.
--- A page that is not a 200 goes to the client as it is. The proxy sends the original request if the page is empty.
---@param context RequestContext
---@param sender Sender
---@param sleeper Sleeper
---@return Response|nil response nil if the proxy must send the original request.
---@return string|nil error The reason for the last failure of the page.
local function answer_count(context, sender, sleeper)
	local total, snapshot = ask_count(context, sender)
	if total == nil then return nil end

	local page = tablex.copy(context)
	page.headers = tablex.copy(context.count.page_headers)
	page.headers["X-DuckLake-Snapshot"] = snapshot

	local response, response_error = send_with_retries(page, sender, sleeper)
	if response == nil or response.status ~= constants.http.ok_status then return response, response_error end
	return add_total(response, total)
end

--- Send the request to PostgREST, and send a read again while the answer is retryable.
--- PostgREST fails for a moment if PgDog loses a standby. Each retry waits twice as long as the one before.
---@param context RequestContext
---@param sender Sender|nil The function that sends one request. The default makes an HTTP call. Tests use a fake.
---@param sleeper Sleeper|nil The function that waits a number of milliseconds. The default is ngx.sleep.
---@return Response|nil response nil if all attempts fail.
---@return string|nil error The reason for the last failure.
function postgrest.query(context, sender, sleeper)
	sender = sender or send
	sleeper = sleeper or function(ms) return ngx.sleep(ms / constants.time.milliseconds_per_second) end

	local aggregate = ask_aggregate(context, sender)
	if aggregate then return aggregate end

	local counted, counted_error = answer_count(context, sender, sleeper)
	if counted or counted_error then return counted, counted_error end

	return send_with_retries(context, sender, sleeper)
end

return postgrest
