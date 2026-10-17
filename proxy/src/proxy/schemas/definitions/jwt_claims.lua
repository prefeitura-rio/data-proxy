-- The fields of the jwt_claims schema: the claims that the proxy reads. Other claims are allowed.

local types = require("tableshape").types

return {
	sub = types.string:is_optional(),
	preferred_username = types.string:is_optional(),
	schemas = (types.string + types.array_of(types.string)):is_optional(),
	role = types.string:is_optional(),
}
