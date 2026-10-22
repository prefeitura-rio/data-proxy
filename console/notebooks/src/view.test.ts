import { describe, expect, it } from 'vitest';
import { viewSql } from './view.ts';

describe('viewSql', () => {
	it('reads a file without a delete file as it is', () => {
		const { registrations, sql } = viewSql('people', [{ data: 'https://s/a', delete: null }]);

		expect(registrations).toEqual([{ name: 'people.d0.parquet', url: 'https://s/a' }]);
		expect(sql).toBe(`CREATE OR REPLACE VIEW "people" AS SELECT * FROM read_parquet('people.d0.parquet')`);
	});

	it('removes the rows of the delete file with an anti join on the row number', () => {
		const { registrations, sql } = viewSql('people', [{ data: 'https://s/a', delete: 'https://s/x' }]);

		expect(registrations.map((entry) => entry.name)).toEqual(['people.d0.parquet', 'people.x0.parquet']);
		expect(sql).toContain("ANTI JOIN read_parquet('people.x0.parquet') x ON d.file_row_number = x.pos");
		expect(sql).toContain('EXCLUDE (file_row_number)');
	});

	it('joins the files of a table with UNION ALL, each with its own delete file', () => {
		const { sql } = viewSql('t', [
			{ data: 'a', delete: 'x' },
			{ data: 'b', delete: null }
		]);

		expect(sql.split('UNION ALL')).toHaveLength(2);
		expect(sql).toContain("read_parquet('t.d1.parquet')");
	});

	it('quotes the name of the view', () => {
		expect(viewSql('we"ird', [{ data: 'a', delete: null }]).sql).toContain('VIEW "we""ird"');
	});
});
