-- The fields of the sync_document schema: the parts of sync.json that the proxy reads.

local types = require("tableshape").types

--- A text that has at least one character.
local filled_text = types.string:length(1, math.huge)

--- The fields of a table in sync.json. The checker keeps the fields that it does not name.
local sync_table = types.shape({
	name = filled_text,
	cache_ttl = types.integer:is_optional(),
}, { open = true })

--- The fields of a schema in sync.json. The checker keeps the fields that it does not name.
local sync_schema = types.shape({
	tables = types.array_of(sync_table):is_optional(),
}, { open = true })

return {
	revision = types.integer:is_optional(),
	noCachePaths = types.array_of(types.string):is_optional(),
	scopes = types.map_of(types.string, filled_text):is_optional(),
	schemas = types.map_of(types.string, sync_schema):is_optional(),
}
