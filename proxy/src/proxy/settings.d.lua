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

--- The result of a reload that worked.
---@class SettingsReload
---@field changed boolean True if the proxy took a new document.
