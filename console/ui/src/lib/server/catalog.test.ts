import { describe, expect, it } from 'vitest';
import { rowsToFiles, type CatalogRow } from './catalog';

const row = (change: Partial<CatalogRow> = {}): CatalogRow => ({
	table_name: 'people',
	schema_path: 'main/',
	schema_relative: true,
	table_path: 'people/',
	table_relative: true,
	file_path: 'part-0.parquet',
	file_relative: true,
	delete_path: null,
	delete_relative: null,
	...change
});

describe('rowsToFiles', () => {
	it('maps each column of the catalog to its field of the grant', () => {
		expect(rowsToFiles([row()])).toEqual([
			{
				table: 'people',
				schemaPath: 'main/',
				schemaRelative: true,
				tablePath: 'people/',
				tableRelative: true,
				filePath: 'part-0.parquet',
				fileRelative: true,
				deletePath: null,
				deleteRelative: false
			}
		]);
	});

	it.each([
		['a relative delete file', { delete_path: 'del.parquet', delete_relative: true }, 'del.parquet', true],
		[
			'an absolute delete file',
			{ delete_path: 's3://b/del.parquet', delete_relative: false },
			's3://b/del.parquet',
			false
		],
		['no delete file', { delete_path: null, delete_relative: null }, null, false]
	])('keeps %s', (_name, change, path, relative) => {
		const [file] = rowsToFiles([row(change)]);

		expect([file?.deletePath, file?.deleteRelative]).toEqual([path, relative]);
	});

	it('keeps the order of the rows and each table name', () => {
		const files = rowsToFiles([row({ table_name: 'a' }), row({ table_name: 'b', file_path: 'part-1.parquet' })]);

		expect(files.map((file) => [file.table, file.filePath])).toEqual([
			['a', 'part-0.parquet'],
			['b', 'part-1.parquet']
		]);
	});

	it('returns nothing for no rows', () => {
		expect(rowsToFiles([])).toEqual([]);
	});
});
