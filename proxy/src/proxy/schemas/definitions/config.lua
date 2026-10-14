-- The fields of the config schema: each reads a PROXY_* variable, converts it, and gives a default.

local constants = require("proxy.constants")
local regex = require("proxy.regex")
local types = require("tableshape").types

--- A text that has at least one character.
local filled_text = types.string:length(1, math.huge)

--- Convert the text of a boolean to true or false. Text that is not a known word stays text.
---@param text string
---@return boolean|string
local function boolean_of(text)
	local word = constants.config.boolean_words[text:lower()]
	if word == nil then return text end
	return word
end

--- The text of a whole number from min to max, converted to a number.
---@param min integer
---@param max number Use math.huge for no limit.
---@return tableshape.Type<integer>
local function whole_number(min, max)
	local range = max == math.huge and ("at least " .. min) or ("from " .. min .. " to " .. max)
	return ((types.pattern(regex.whole_number) / tonumber) * types.range(min, max)):describe("whole number " .. range)
end

--- Accept a missing value too, and give the default for it.
---@generic T, D
---@param field_type tableshape.Type<T>
---@param default D
---@return tableshape.Type<T|D>
local function with_default(field_type, default) return field_type + (types.null / default) end

return {
	cache_ttl = with_default(whole_number(0, math.huge), 300),
	max_body = with_default(whole_number(0, math.huge), 262144),
	ha_mode = with_default(((types.string / boolean_of) * types.boolean):describe("true, false, 1 or 0"), false),
	redis_host = filled_text,
	redis_port = with_default(whole_number(1, 65535), 6379),
	redis_database = with_default(whole_number(0, math.huge), 1),
	redis_password = types.string:is_optional(),
	postgrest_read = filled_text,
	postgrest_write = filled_text,
	postgrest_retries = with_default(whole_number(0, 10), 1),
	postgrest_backoff_ms = with_default(whole_number(0, 60000), 0),
	max_inflight = with_default(whole_number(0, math.huge), 0),
	config_refresh_seconds = with_default(whole_number(1, math.huge), 60),
}
