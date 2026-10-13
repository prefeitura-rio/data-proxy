-- Table settings from sync.json: the cache time of each table and the paths that are never cached.

local List = require("pl.List") --[[@as pl.ListClass]]
local cjson = require("cjson.safe")
local constants = require("proxy.constants")
local file = require("pl.file") --[[@as pl.file]]
local regex = require("proxy.regex")
local schemas = require("proxy.schemas.main")
local tablex = require("pl.tablex") --[[@as pl.tablex]]

--- Functions that read sync.json.
---@class SyncModule
local sync = {}

--- Read sync.json and return the table settings that the proxy uses.
--- The function raises an error if the file cannot be opened, is not valid JSON, or does not match
--- schemas.sync_document. This way the proxy never runs with settings that it cannot use.
---@param path string Path of sync.json, for example /config/sync.json.
---@return SyncConfig
function sync.load(path)
	local content, read_error = file.read(path)
	if content == nil then error(read_error) end

	local decoded, decode_error = cjson.decode(content)
	if decoded == nil then error(decode_error) end

	local document, schema_error = schemas.sync_document:validate(decoded)
	if document == nil then error("Invalid sync.json: " .. tostring(schema_error)) end

	---@type table<string, TableSettings>
	local tables = {}

	tablex.foreach(document.schemas or {}, function(schema)
		List(schema.tables or {}):foreach(function(config)
			local short_name = config.name:match(regex.table_name)
			if short_name then tables[short_name] = { cache_ttl = config.cache_ttl } end
		end)
	end)

	return {
		tables = tables,
		disabled_endpoints = document.noCachePaths or constants.disabled_endpoints,
	}
end

--- Find the settings of the table in a request path.
---
--- ```lua
--- sync.table_for(cfg, "/people?id=eq.1")  --> cfg.tables.people
--- sync.table_for(cfg, "/people/1")        --> cfg.tables.people
--- sync.table_for(cfg, "/unknown")         --> nil
--- ```
---@param cfg SyncConfig
---@param uri string Request path.
---@return TableSettings|nil settings nil if the table has no settings.
function sync.table_for(cfg, uri)
	local short_name = uri:match(regex.table_path)
	return short_name and cfg.tables[short_name]
end

return sync
