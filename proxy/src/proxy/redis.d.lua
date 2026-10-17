---@meta

--- The Redis cache store: it reads and writes cached answers, with one connection for each call.
---@class Redis: pl.Instance
---@field config Config The config that gives the nodes, the password and the database.
