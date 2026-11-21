# The console and the access profiles belong to the cluster. The proxy refuses them, so that the config endpoint
# that the proxy itself calls is not reachable from outside.
GET http://{{host}}/rpc/config
Accept-Profile: console

HTTP 403
[Asserts]
jsonpath "$.error" contains "profile is not available"

POST http://{{host}}/schemas
Content-Profile: console
Content-Type: application/json
```
{"name": "alpha"}
```

HTTP 403

GET http://{{host}}/policy
Accept-Profile: access

HTTP 403

# A data path names a project and a schema, and the schema of the path replaces any profile that the client sends.
GET http://{{host}}/default/test/people

HTTP 200

GET http://{{host}}/default/test/people
Accept-Profile: other

HTTP 200

# A path that names no known schema is not found, and so is a path without a project.
GET http://{{host}}/people

HTTP 404
[Asserts]
jsonpath "$.error" contains "schema is not known"

GET http://{{host}}/default/nope/people

HTTP 404

# The first segments of the host belong to its services, also when a schema has the same name.
GET http://{{host}}/console/test/people

HTTP 404

GET http://{{host}}/docs/test/people

HTTP 404
