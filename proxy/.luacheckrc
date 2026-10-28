std = "ngx_lua"
max_line_length = 120
max_cyclomatic_complexity = 8

-- A method of a class without state does not use self.
ignore = { "212/self" }

-- .types/ has definition files only. Their functions have no body, so luacheck warns about unused arguments.
-- .lux/ holds the installed rocks. A task that installs the test rocks must not add their files to the lint.
exclude_files = { ".types/**", ".lux/**" }
