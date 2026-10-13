std = "ngx_lua"
max_line_length = 120
max_cyclomatic_complexity = 8

-- A method of a class without state does not use self.
ignore = { "212/self" }

-- .types/ has definition files only. Their functions have no body, so luacheck warns about unused arguments.
exclude_files = { ".types/**" }
