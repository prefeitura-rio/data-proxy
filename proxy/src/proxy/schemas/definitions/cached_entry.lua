-- The fields of the cached_entry schema: an answer that the cache stored in Redis.

local types = require("tableshape").types

return {
	body = types.string,
	headers = types.map_of(types.string, types.string):is_optional(),
}
