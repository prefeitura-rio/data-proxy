-- The table settings of the proxy: shared by every worker, and replaced when sync.json changes.

local sync = require("proxy.sync")

--- The functions that keep the settings in a shared dictionary and read them again after a change.
---@class SettingsModule
local settings = {}

--- Take a new sync.json for every worker: store its text and move the version.
---@param store SettingsStore The shared dictionary.
---@param document string The text of a valid sync.json.
---@return nil
function settings.replace(store, document)
	store:set("document", document)
	store:incr("version", 1, 0)
end

--- Read sync.json again and replace the shared settings when the document is new.
--- The shared settings stay as they are if the file does not exist yet, cannot be read, or holds
--- a document that is not valid.
---@param store SettingsStore The shared dictionary.
---@param path string Path of sync.json.
---@param read fun(path: string): string|nil, string|nil Gives the text, nil for no file, or nil and a reason.
---@return SettingsReload|nil result nil if the reload failed.
---@return string|nil reason Why the reload failed.
function settings.reload(store, path, read)
	local text, failure = read(path)
	if text == nil then
		if failure then return nil, failure end
		return { changed = false }
	end
	if text == store:get("document") then return { changed = false } end

	local config, reason = sync.parse(text)
	if config == nil then return nil, reason end

	settings.replace(store, text)
	return { changed = true }
end

--- Give the settings that one worker uses now. The worker parses the shared document again only
--- when its version moved since the last call.
---@param store SettingsStore The shared dictionary.
---@param cache SettingsCache The state of this worker. The function updates it.
---@return SyncConfig
function settings.current(store, cache)
	local version = store:get("version") or 0
	if cache.version ~= version then
		cache.value = sync.parse(store:get("document") or "{}") or assert(sync.parse("{}"))
		cache.version = version
	end
	return cache.value --[[@as SyncConfig]]
end

return settings
