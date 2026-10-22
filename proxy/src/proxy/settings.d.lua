---@meta

--- A shared dictionary, such as ngx.shared.proxy_settings.
---@class SettingsStore
---@field get fun(self: SettingsStore, key: string): any
---@field set fun(self: SettingsStore, key: string, value: any): boolean
---@field incr fun(self: SettingsStore, key: string, delta: number, init: number): number

--- The state that one worker keeps between two requests.
---@class SettingsCache
---@field version integer|nil The version of the settings that the worker parsed last.
---@field value SyncConfig|nil The settings that the worker parsed last.

--- The answer of the console to a request for sync.json.
---@class SettingsResponse
---@field status integer HTTP status.
---@field body string The text of the answer.
---@field etag string|nil The ETag header, which is the revision of the config.

--- The result of a reload that worked.
---@class SettingsReload
---@field changed boolean True if the proxy took a new document.
---@field revision string The revision of the document that the console gave.
