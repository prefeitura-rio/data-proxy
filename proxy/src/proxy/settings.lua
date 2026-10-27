-- The table settings of the proxy: shared by every worker, and replaced when the console changes them.

local constants = require("proxy.constants")
local sync = require("proxy.sync")

--- The functions that keep the settings in a shared dictionary and read them again after a change.
---@class SettingsModule
local settings = {}

--- Take a new sync.json for every worker: store its text and revision, and move the version.
---@param store SettingsStore The shared dictionary.
---@param document string The text of a valid sync.json.
---@param revision string The revision that the text came from.
---@return nil
function settings.replace(store, document, revision)
	store:set("document", document)
	store:set("revision", revision)
	store:incr("version", 1, 0)
end

--- Fetch sync.json from the console and replace the shared settings when the document is new.
--- The shared settings stay as they are if the console fails or gives a document that is not valid.
---@param store SettingsStore The shared dictionary.
---@param url string Where the console serves sync.json.
---@param fetch fun(url: string): SettingsResponse|nil, string|nil Gives the answer of the console, or nil and a reason.
---@return SettingsReload|nil result nil if the reload failed.
---@return string|nil reason Why the reload failed.
function settings.reload(store, url, fetch)
	local response, failure = fetch(url)
	if response == nil then return nil, failure end
	if response.status ~= constants.http.ok_status then return nil, "The console answered " .. response.status end

	local revision = response.etag or ""
	local same_revision = revision ~= "" and revision == store:get("revision")
	if same_revision or response.body == store:get("document") then return { changed = false, revision = revision } end

	local config, reason = sync.parse(response.body)
	if config == nil then return nil, reason end

	settings.replace(store, response.body, revision)
	return { changed = true, revision = revision }
end

--- Give the settings that one worker uses now. The worker parses the shared document again only
--- when its version moved since the last call.
---@param store SettingsStore The shared dictionary.
---@param cache SettingsCache The state of this worker. The function updates it.
---@return SyncConfig
function settings.current(store, cache)
	local version = store:get("version") or 0
	if cache.version ~= version then
		cache.value = sync.parse(store:get("document") or "{}") or sync.empty()
		cache.version = version
	end
	return cache.value --[[@as SyncConfig]]
end

--- Ask the console for sync.json over HTTP. Call it where nginx allows sockets: a timer or a request.
---@param url string Where the console serves sync.json.
---@return SettingsResponse|nil response nil if the console cannot be reached.
---@return string|nil reason Why the console cannot be reached.
function settings.fetch(url)
	local client, client_error = require("resty.http").new()
	if client == nil then return nil, client_error end
	local limits = constants.settings
	client:set_timeouts(limits.connect_ms, limits.send_ms, limits.read_ms)
	local response, reason = client:request_uri(url, {
		headers = { Accept = "application/json", ["Accept-Profile"] = constants.internal_profile },
	})
	if response == nil then return nil, reason end
	return { status = response.status, body = response.body, etag = response.headers["ETag"] }
end

return settings
