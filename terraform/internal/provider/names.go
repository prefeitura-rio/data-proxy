package provider

import (
	"fmt"
	"strings"
)

// maxNameBytes is the longest physical name of a schema: the project, two underscores and the short name. The
// project `default` counts with its own name.
const maxNameBytes = 56

// reservedProjects are the project names that a URL or a PostgreSQL schema of the system uses. The pattern of a
// project name has no underscore, so `data_proxy`, `information_schema` and `pg_*` can't match it.
var reservedProjects = []string{"access", "console", "rbac", "dbos", "public", "duckdb", "rpc", "docs", "v1"}

// schemaProblem returns the reason that a new schema can't have this name, or an empty text. The console gives the
// same messages.
func schemaProblem(project, shortName string) string {
	if strings.Contains(shortName, "__") {
		return "A schema name can't contain two underscores in a row."
	}

	full := project + "__" + shortName
	if len(full) > maxNameBytes {
		return fmt.Sprintf("The name %s has %d bytes. The limit is %d.", full, len(full), maxNameBytes)
	}

	return ""
}

// physicalSchemaName returns the name of the schema in the database: the project, two underscores and the short
// name. The project `default` counts as a project.
func physicalSchemaName(project, shortName string) string {
	if project == "" {
		project = defaultProject
	}

	return project + "__" + shortName
}

// legacySchemaName returns the name of a schema without the double underscore: the short name in the project
// `default`, and `<project>_<schema>` in another project. A name without the form is its own legacy name.
func legacySchemaName(name string) string {
	project, shortName, found := strings.Cut(name, "__")

	switch {
	case !found:
		return name
	case project == defaultProject:
		return shortName
	default:
		return project + "_" + shortName
	}
}

// renamedSchema reports whether the state holds the legacy name of the schema that the plan names, so that a table
// keeps its row when the database renames the schema.
func renamedSchema(state, plan string) bool {
	return state != plan && legacySchemaName(plan) == state
}
