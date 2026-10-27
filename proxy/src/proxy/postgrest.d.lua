---@meta

--- An HTTP answer from PostgREST or from the cache.
---@class Response
---@field status integer HTTP status code.
---@field body string Response body.
---@field headers table<string,string> Response headers that are passed on to the client.

--- A function that sends one request to one PostgREST service. It gives the answer or the reason for a failure.
---@alias Sender fun(context: RequestContext, target: string): Response|nil, string|nil

--- A function that waits a number of milliseconds. The default is ngx.sleep.
---@alias Sleeper fun(ms: integer)
