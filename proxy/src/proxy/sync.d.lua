---@meta

--- The settings of one table from sync.json.
---@class TableSettings
---@field cache_ttl number|nil Cache time in seconds. nil means use config.cache_ttl.

--- A table in sync.json.
---@class SyncTable
---@field name string Name of the table, for example project.schema.people.
---@field cache_ttl integer|nil Cache time of the table in seconds.

--- A schema in sync.json.
---@class SyncSchema
---@field tables SyncTable[]|nil The tables of the schema.

--- The parts of sync.json that the proxy reads.
---@class SyncDocument
---@field revision integer|nil The revision of the access policy and the rules, a counter of the database.
---@field noCachePaths string[]|nil Path prefixes that are never cached.
---@field scopes table<string, string>|nil The scope of each subject: the digest of its grants.
---@field schemas table<string, SyncSchema>|nil The schemas, by name.

--- The table settings that the proxy reads from sync.json.
---@class SyncConfig
---@field tables table<string, TableSettings> Settings by short table name.
---@field disabled_endpoints string[] Path prefixes that are never cached.
---@field revision integer|nil The revision of the access policy and the rules that the document carries.
---@field scopes table<string, string>|nil The scope of each subject. Subjects with the same scope have the same grants.

--- The schema of sync.json.
---@class SchemasModule
---@field sync_document Schema<SyncDocument>
