import { describe, expect, it, vi } from 'vitest';
import { buildGrant, createGrant, prefixOf, resolvePath, TTL_SECONDS, type FileRow } from './grant';
import type { Result } from './postgrest';

const row = (overrides: Partial<FileRow> = {}): FileRow => ({
	table: 'people',
	schemaPath: 'main/',
	schemaRelative: true,
	tablePath: 'people/',
	tableRelative: true,
	filePath: 'a.parquet',
	fileRelative: true,
	deletePath: null,
	deleteRelative: false,
	...overrides
});

const sign = (key: string) => `https://signed/${key}?sig=1`;

describe('resolvePath', () => {
	it('joins the relative parts under the data path', () => {
		expect(
			resolvePath('s3://data/ducklake/sales/', [
				['main/', true],
				['people/', true],
				['a.parquet', true]
			])
		).toBe('s3://data/ducklake/sales/main/people/a.parquet');
	});

	it('keeps an absolute path as it is', () => {
		expect(
			resolvePath('s3://data/x/', [
				['main/', true],
				['s3://data/y/f.parquet', false]
			])
		).toBe('s3://data/y/f.parquet');
	});

	it('refuses a path that leaves its folder', () => {
		expect(() =>
			resolvePath('s3://data/x/', [
				['../other/', true],
				['a.parquet', true]
			])
		).toThrow('outside');
	});
});

describe('buildGrant', () => {
	const options = { schema: 'sales', dataPath: 's3://data/ducklake/sales/', bucket: 'data', sign, now: 1_000_000 };

	it('expires the links after five minutes', async () => {
		expect(TTL_SECONDS).toBe(300);
		expect((await buildGrant({ ...options, rows: [row()] })).expiresAt).toBe(1_000_000 + 300_000);
	});

	it('signs a data file without a delete file', async () => {
		expect((await buildGrant({ ...options, rows: [row()] })).tables).toEqual([
			{
				name: 'people',
				files: [{ data: 'https://signed/ducklake/sales/main/people/a.parquet?sig=1', delete: null }]
			}
		]);
	});

	it('signs the delete file next to its data file', async () => {
		const grant = await buildGrant({
			...options,
			rows: [row({ deletePath: 'a-delete.parquet', deleteRelative: true })]
		});

		expect(grant.tables[0]?.files[0]).toEqual({
			data: 'https://signed/ducklake/sales/main/people/a.parquet?sig=1',
			delete: 'https://signed/ducklake/sales/main/people/a-delete.parquet?sig=1'
		});
	});

	it('signs only the paths that the rows list', async () => {
		const signed = vi.fn(sign);

		await buildGrant({ ...options, sign: signed, rows: [row(), row({ filePath: 'b.parquet' })] });

		expect(signed.mock.calls.map(([key]) => key)).toEqual([
			'ducklake/sales/main/people/a.parquet',
			'ducklake/sales/main/people/b.parquet'
		]);
	});

	it('groups the files of each table', async () => {
		const grant = await buildGrant({
			...options,
			rows: [row(), row({ table: 'orders', tablePath: 'orders/' }), row({ filePath: 'b.parquet' })]
		});

		expect(grant.tables.map((table) => [table.name, table.files.length])).toEqual([
			['people', 2],
			['orders', 1]
		]);
	});

	it('refuses a file in another bucket', async () => {
		await expect(
			buildGrant({ ...options, rows: [row({ filePath: 's3://other/a.parquet', fileRelative: false })] })
		).rejects.toThrow('bucket');
	});
});

describe('prefixOf', () => {
	it('gives the folder of the schema in the bucket, with a trailing slash', () => {
		expect(prefixOf('s3://data/ducklake/sales', 'data')).toBe('ducklake/sales/');
		expect(prefixOf('s3://data/ducklake/sales/', 'data')).toBe('ducklake/sales/');
	});

	it('refuses a data path in another bucket', () => {
		expect(() => prefixOf('s3://other/x/', 'data')).toThrow('bucket');
	});
});

describe('createGrant', () => {
	const ok = (): Result<unknown> => ({ ok: true, data: [] });
	const deps = (overrides = {}) => ({
		authorize: vi.fn(() => Promise.resolve(ok())),
		list: vi.fn(() => Promise.resolve({ dataPath: 's3://data/ducklake/sales/', rows: [row()] })),
		signerFor: vi.fn(() => Promise.resolve(sign)),
		bucket: 'data',
		now: 0,
		...overrides
	});

	it('lists and signs after every table is authorized', async () => {
		const result = await createGrant('sales', ['p.d.people'], deps());

		expect(result).toMatchObject({ ok: true, data: { schema: 'sales', expiresAt: 300_000 } });
	});

	it('lists only the tables that the database authorized, by their destination name', async () => {
		const d = deps();

		await createGrant('sales', ['p.d.people', 'p.d.orders'], d);

		expect(d.list).toHaveBeenCalledWith('sales', ['people', 'orders']);
	});

	it.each([403, 409])('stops with the %i of the database and lists nothing', async (status) => {
		const d = deps({ authorize: vi.fn(() => Promise.resolve({ ok: false, status, message: 'no' })) });

		const result = await createGrant('sales', ['p.d.people'], d);

		expect(result).toEqual({ ok: false, status, message: 'no' });
		expect(d.list).not.toHaveBeenCalled();
	});

	it('asks for the signer of the data path that the catalog gave', async () => {
		const d = deps();

		await createGrant('sales', ['p.d.people'], d);

		expect(d.signerFor).toHaveBeenCalledWith('s3://data/ducklake/sales/');
	});

	it('refuses a schema name that is not an identifier', async () => {
		const d = deps();

		expect(await createGrant('sales"; DROP', ['p.d.people'], d)).toMatchObject({ ok: false, status: 400 });
		expect(d.authorize).not.toHaveBeenCalled();
	});

	it('refuses a request without tables', async () => {
		expect(await createGrant('sales', [], deps())).toMatchObject({ ok: false, status: 400 });
	});
});
