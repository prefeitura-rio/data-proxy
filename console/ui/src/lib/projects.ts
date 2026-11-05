import { optional, text } from '#lib/forms.js';

export const DEFAULT_PROJECT = 'default';

/** The name that the database, the sync and the API give a schema: its short name, with the prefix of its project unless the project is default. */
export const physicalName = (project: string, shortName: string) =>
	project === '' || project === DEFAULT_PROJECT ? shortName : `${project}_${shortName}`;

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
