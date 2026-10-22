-- The registry of the schemas of the data that the proxy does not create and of its config.

local Schema = require("proxy.schemas.schema")
local dir = require("pl.dir") --[[@as pl.dir]]
local path = require("pl.path") --[[@as pl.path]]
local regex = require("proxy.regex")
local tablex = require("pl.tablex") --[[@as pl.tablex]]

--- The registry of the schemas. A schema is a field of the registry, by name: schemas.cached_entry.
---@class SchemasModule
local schemas = {}

--- The folder of the schema definitions: definitions, next to this module.
---@return string directory
local function definition_directory()
	local file = package.searchpath("proxy.schemas.main", package.path)
	if file == nil then error("Can't find the schema folder: proxy.schemas.main is not in package.path") end
	return path.join(path.dirname(file), "definitions")
end

--- Register every file <name>.lua of the definitions folder as the schema <name>.
--- A file gives the fields of its schema, and the schema makes the checker for them.
--- The module loads the definitions once, so a caller reads them without a call.
---@return nil
local function load_definitions()
	local folder = definition_directory()
	local files = dir.getfiles(folder, "*.lua")
	if #files == 0 then error("No schema definitions in " .. folder) end

	tablex.foreach(files, function(file)
		local name = path.basename(file):match(regex.module_file)

		if name then rawset(schemas, name, Schema(name, require("proxy.schemas.definitions." .. name))) end
	end)
end

load_definitions()

return schemas
