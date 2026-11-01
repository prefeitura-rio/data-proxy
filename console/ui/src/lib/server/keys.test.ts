import { beforeEach, describe, expect, it, vi } from 'vitest';
import { catalogKeys } from './keys';

type Result = { rows: Record<string, string>[] };

const client = vi.hoisted(() => ({
	connect: vi.fn<() => Promise<void>>(),
	query: vi.fn<(sql: string, params?: string[]) => Promise<Result>>(),
	end: vi.fn<() => Promise<void>>()
}));

vi.mock('pg', () => ({
	default: {
		Client: class {
			connect = client.connect;
			query = client.query;
			end = client.end;
		}
	}
}));

const keys = catalogKeys('postgresql://signer@catalog/ducklake');
const statements = () => client.query.mock.calls.map(([sql]) => sql.split('\n')[0]?.trim());

beforeEach(() => {
	client.connect.mockReset().mockResolvedValue();
	client.query.mockReset().mockResolvedValue({ rows: [] });
	client.end.mockReset().mockResolvedValue();
});

describe('catalogKeys.get', () => {
	it('reads the key of the schema with the schema as a parameter', async () => {
		client.query.mockResolvedValue({ rows: [{ access_key: 'AK', secret_sealed: 'sealed' }] });

		expect(await keys.get('sales')).toEqual({ accessKeyId: 'AK', sealed: 'sealed' });
		expect(client.query.mock.calls[0]?.[1]).toEqual(['sales']);
		expect(client.end).toHaveBeenCalledOnce();
	});

	it('gives null for a schema without a key', async () => {
		expect(await keys.get('sales')).toBeNull();
	});

	it('closes the connection when the query fails', async () => {
		client.query.mockRejectedValue(new Error('no table'));

		await expect(keys.get('sales')).rejects.toThrow('no table');
		expect(client.end).toHaveBeenCalledOnce();
	});
});

describe('catalogKeys.put', () => {
	it('writes the key with the values as parameters', async () => {
		await keys.put('sales', 'AK', 'blob-1');

		expect(client.query.mock.calls[0]?.[1]).toEqual(['sales', 'AK', 'blob-1']);
		expect(client.query.mock.calls[0]?.[0]).not.toContain('blob-1');
		expect(client.end).toHaveBeenCalledOnce();
	});
});

describe('catalogKeys.locked', () => {
	it('holds the lock of the schema while the work runs, and returns its result', async () => {
		const work = vi.fn(() => {
			expect(statements()).toEqual(['SELECT pg_advisory_lock(hashtext($1))']);

			return Promise.resolve('done');
		});

		expect(await keys.locked('sales', work)).toBe('done');
		expect(statements()).toEqual(['SELECT pg_advisory_lock(hashtext($1))', 'SELECT pg_advisory_unlock(hashtext($1))']);
		expect(client.query.mock.calls.map(([, params]) => params)).toEqual([['signer:sales'], ['signer:sales']]);
		expect(client.end).toHaveBeenCalledOnce();
	});

	it('frees the lock when the work throws', async () => {
		await expect(keys.locked('sales', () => Promise.reject(new Error('seal failed')))).rejects.toThrow('seal failed');

		expect(statements()).toEqual(['SELECT pg_advisory_lock(hashtext($1))', 'SELECT pg_advisory_unlock(hashtext($1))']);
		expect(client.end).toHaveBeenCalledOnce();
	});
});
