local helpers = require("tests.unit.helpers")
local parser = require("proxy.parser.main")

local check = helpers.check(describe, it)

check(
	"parser.parse",
	function(case) return parser.parse(case.query, case.options) end,
	helpers.load_filter_cases("tests/unit/fixtures/filters.json")
)
