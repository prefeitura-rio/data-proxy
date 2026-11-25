import { describe, expect, it } from 'vitest';
import { groupByProject, physicalName, projectProblem, schemaPayload, schemaProblem } from './projects';

const form = (entries: Record<string, string>) => {
	const data = new FormData();

	for (const [name, value] of Object.entries(entries)) {
		data.set(name, value);
	}

	return data;
};

describe('physicalName', () => {
	it.each([
		['a project and a short name make the name', 'sales', 'orders', 'sales__orders'],
		['default has its prefix too', 'default', 'orders', 'default__orders'],
		['the short name keeps its underscores', 'sales', 'a_b', 'sales__a_b'],
		['no project means default', '', 'orders', 'default__orders']
	])('%s', (_name, project, shortName, expected) => {
		expect(physicalName(project, shortName)).toBe(expected);
	});
});

describe('schemaPayload', () => {
	it('sends the project and the short name, and not the physical name', () => {
		const body = schemaPayload(form({ project: 'sales', short_name: ' orders ', claim: 'cpf', encrypted: 'on' }));

		expect(body).toEqual({
			project: 'sales',
			short_name: 'orders',
			source: 'bigquery',
			claim: 'cpf',
			encrypted: true
		});
		expect(body).not.toHaveProperty('name');
	});

	it.each([
		['a project of default', { project: 'default', short_name: 'hr' }, 'default'],
		['a form with no project', { short_name: 'hr' }, 'default']
	])('puts %s in default', (_name, entries, project) => {
		expect(schemaPayload(form(entries)).project).toBe(project);
	});

	it('keeps the source that the form names, and leaves a blank claim empty', () => {
		const body = schemaPayload(form({ project: 'sales', short_name: 'x', source: 'other', claim: ' ' }));

		expect(body.source).toBe('other');
		expect(body.claim).toBeNull();
		expect(body.encrypted).toBe(false);
	});
});

describe('groupByProject', () => {
	const schema = (project: string, shortName: string) => ({
		project,
		short_name: shortName,
		name: physicalName(project, shortName)
	});

	it('groups the schemas by project, in the order of the projects, and keeps an empty project', () => {
		const groups = groupByProject(
			[{ name: 'default' }, { name: 'empty' }, { name: 'sales' }],
			[schema('sales', 'orders'), schema('default', 'hr'), schema('sales', 'crm')]
		);

		expect(groups.map((group) => [group.project, group.schemas.map((item) => item.name)])).toEqual([
			['default', ['default__hr']],
			['empty', []],
			['sales', ['sales__crm', 'sales__orders']]
		]);
	});

	it('shows the schema of a project that the user cannot read, under its project name', () => {
		const groups = groupByProject([], [schema('hr', 'staff')]);

		expect(groups.map((group) => group.project)).toEqual(['hr']);
	});
});

describe('projectProblem', () => {
	it.each([
		['a reserved name', 'access', 'The project name access is reserved.'],
		['the name of a console route', 'docs', 'The project name docs is reserved.'],
		['a free name', 'sales', null],
		['a name that only starts like a reserved one', 'accessx', null]
	])('%s', (_name, name, expected) => {
		expect(projectProblem(name)).toBe(expected);
	});
});

describe('schemaProblem', () => {
	it.each([
		['a plain name', 'sales', 'orders', null],
		['a single underscore', 'sales', 'a_b_', null],
		['two underscores in a row', 'sales', 'a__b', "A schema name can't contain two underscores in a row."],
		['exactly 56 bytes', 'sales', 'a'.repeat(49), null],
		['57 bytes', 'sales', 'a'.repeat(50), `The name sales__${'a'.repeat(50)} has 57 bytes. The limit is 56.`],
		['default counts as a project name', 'default', 'a'.repeat(47), null],
		[
			'default with one byte too many',
			'default',
			'a'.repeat(48),
			`The name default__${'a'.repeat(48)} has 57 bytes. The limit is 56.`
		],
		[
			'no project means default',
			'',
			'a'.repeat(48),
			`The name default__${'a'.repeat(48)} has 57 bytes. The limit is 56.`
		],
		[
			'a multibyte letter counts in bytes',
			'sales',
			`${'a'.repeat(48)}é`,
			`The name sales__${'a'.repeat(48)}é has 57 bytes. The limit is 56.`
		]
	])('%s', (_name, project, shortName, expected) => {
		expect(schemaProblem(project, shortName)).toBe(expected);
	});
});
