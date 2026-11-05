import { describe, expect, it } from 'vitest';
import { groupByProject, physicalName, schemaPayload } from './projects';

const form = (entries: Record<string, string>) => {
	const data = new FormData();

	for (const [name, value] of Object.entries(entries)) {
		data.set(name, value);
	}

	return data;
};

describe('physicalName', () => {
	it.each([
		['a project adds its prefix', 'sales', 'orders', 'sales_orders'],
		['default adds no prefix', 'default', 'orders', 'orders'],
		['the short name keeps its underscores', 'sales', 'a_b', 'sales_a_b'],
		['no project means default', '', 'orders', 'orders']
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
			['default', ['hr']],
			['empty', []],
			['sales', ['sales_crm', 'sales_orders']]
		]);
	});

	it('shows the schema of a project that the user cannot read, under its project name', () => {
		const groups = groupByProject([], [schema('hr', 'staff')]);

		expect(groups.map((group) => group.project)).toEqual(['hr']);
	});
});
