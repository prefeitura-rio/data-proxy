-- The Lua patterns that the proxy matches text with, in one place, named after what they match.

---@type Regex
return {
	bearer_payload = "^Bearer [^.]+%.([^%.]+)",
	column_name = "^[%a_][%w_]*$",
	escaped_slash = "%%2F",
	media_range = "^[^,;]+",
	module_file = "^(.+)%.lua$",
	parameter = "^([^=]*)=(.*)$",
	percent_escape = "^[0-9A-Fa-f][0-9A-Fa-f]$",
	table_name = "([^.]+)$",
	table_path = "^/([^/?]+)",
	whole_number = "^%d+$",
}
