import { optional, text } from '#lib/forms.js';

export const DEFAULT_PROJECT = 'default';

/** The longest physical name of a schema: `<project>__<short name>`, with `default` as the project name. */
export const MAX_NAME_BYTES = 56;

/** Project names that a URL or a PostgreSQL schema of the system uses. The pattern of a project name has no underscore, so `data_proxy`, `information_schema` and `pg_*` can't match it. */
export const RESERVED_PROJECTS = ['access', 'console', 'rbac', 'dbos', 'public', 'duckdb', 'rpc', 'docs', 'v1'];

/** The reason that a new project can't have this name, or null. */
export const projectProblem = (name: string) =>
	RESERVED_PROJECTS.includes(name) ? `The project name ${name} is reserved.` : null;

/** The reason that a new schema can't have this name, or null. */
export const schemaProblem = (project: string, shortName: string) => {
	if (shortName.includes('__')) {
		return "A schema name can't contain two underscores in a row.";
	}

	const full = `${project || DEFAULT_PROJECT}__${shortName}`;
	const bytes = new TextEncoder().encode(full).length;

	return bytes > MAX_NAME_BYTES ? `The name ${full} has ${bytes} bytes. The limit is ${MAX_NAME_BYTES}.` : null;
};

/** The name that the database and the sync give a schema: its project, two underscores and its short name. */
export const physicalName = (project: string, shortName: string) =>
	`${project === '' ? DEFAULT_PROJECT : project}__${shortName}`;

/** The body of the request that adds a schema. The database builds the name from the project and the short name. */
export const schemaPayload = (form: FormData) => ({
	project: text(form, 'project') || DEFAULT_PROJECT,
	short_name: text(form, 'short_name'),
	source: text(form, 'source') || 'bigquery',
	claim: optional(form, 'claim'),
	encrypted: form.get('encrypted') === 'on'
});

/** Group the schemas by project, in the order of the projects. A project with no schema stays, and so does a project that the user cannot read but has a schema in. */
export const groupByProject = <T extends { project: string; name: string }>(
	projects: { name: string }[],
	schemas: T[]
) => {
	const names = [
		...new Set([...projects.map((project) => project.name), ...schemas.map((schema) => schema.project)])
	].sort();

	return names.map((project) => ({
		project,
		schemas: schemas.filter((schema) => schema.project === project).sort((a, b) => a.name.localeCompare(b.name))
	}));
};
