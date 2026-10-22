import { describe, expect, it, vi } from 'vitest';
import { identityFor, readPolicy, userName, type Iam, type KeyStore } from './identities';

const location = { bucket: 'data', prefix: 'ducklake/sales/' };

function fakes(stored: { accessKeyId: string; sealed: string } | null = null) {
	const rows = new Map<string, { accessKeyId: string; sealed: string }>();

	if (stored) {
		rows.set('sales', stored);
	}

	const iam: Iam = {
		ensureUser: vi.fn(() => Promise.resolve()),
		putPolicy: vi.fn(() => Promise.resolve()),
		replaceKey: vi.fn(() => Promise.resolve({ accessKeyId: 'AKIANEW', secretAccessKey: 'new-secret' }))
	};
	const store: KeyStore = {
		get: vi.fn((schema: string) => Promise.resolve(rows.get(schema) ?? null)),
		put: vi.fn((schema: string, accessKeyId: string, sealed: string) => {
			rows.set(schema, { accessKeyId, sealed });

			return Promise.resolve();
		}),
		locked: (_schema, run) => run()
	};
	const seal = (text: string) => `sealed:${text}`;
	const unseal = (text: string) => (text.startsWith('sealed:') ? text.slice(7) : null);

	return { iam, store, seal, unseal };
}

describe('readPolicy', () => {
	it('allows reading the objects under one prefix and nothing else', () => {
		expect(readPolicy('data', 'ducklake/sales/')).toEqual({
			Version: '2012-10-17',
			Statement: [{ Effect: 'Allow', Action: ['s3:GetObject'], Resource: ['arn:aws:s3:::data/ducklake/sales/*'] }]
		});
	});
});

describe('userName', () => {
	it('names the identity after the schema', () => {
		expect(userName('sales')).toBe('sig_sales');
	});
});

describe('identityFor', () => {
	it('creates the user, the policy and a key for a schema that has none, and keeps the key', async () => {
		const deps = fakes();

		const credentials = await identityFor('sales', location, deps);

		expect(credentials).toEqual({ accessKeyId: 'AKIANEW', secretAccessKey: 'new-secret' });
		expect(deps.iam.ensureUser).toHaveBeenCalledWith('sig_sales');
		expect(deps.iam.putPolicy).toHaveBeenCalledWith('sig_sales', readPolicy('data', 'ducklake/sales/'));
		expect(deps.store.put).toHaveBeenCalledWith('sales', 'AKIANEW', 'sealed:new-secret');
	});

	it('uses the stored key and calls no storage API the second time', async () => {
		const deps = fakes();
		await identityFor('sales', location, deps);
		vi.mocked(deps.iam.ensureUser).mockClear();

		const second = await identityFor('sales', location, deps);

		expect(second.accessKeyId).toBe('AKIANEW');
		expect(deps.iam.ensureUser).not.toHaveBeenCalled();
		expect(deps.iam.replaceKey).toHaveBeenCalledTimes(1);
	});

	it('makes a new key when the stored secret can not be opened', async () => {
		const deps = fakes({ accessKeyId: 'AKIAOLD', sealed: 'garbage' });

		const credentials = await identityFor('sales', location, deps);

		expect(credentials.accessKeyId).toBe('AKIANEW');
		expect(deps.iam.replaceKey).toHaveBeenCalledWith('sig_sales');
	});

	it('does not make a second key when another replica made one while this call waited for the lock', async () => {
		const deps = fakes();
		const rows = { accessKeyId: 'AKIAOTHER', sealed: 'sealed:other-secret' };
		vi.mocked(deps.store.get).mockResolvedValueOnce(null).mockResolvedValueOnce(rows);

		const credentials = await identityFor('sales', location, deps);

		expect(credentials).toEqual({ accessKeyId: 'AKIAOTHER', secretAccessKey: 'other-secret' });
		expect(deps.iam.replaceKey).not.toHaveBeenCalled();
	});

	it('keeps the identities of two schemas apart', async () => {
		const deps = fakes();
		vi.mocked(deps.iam.replaceKey)
			.mockResolvedValueOnce({ accessKeyId: 'AKIAONE', secretAccessKey: 's1' })
			.mockResolvedValueOnce({ accessKeyId: 'AKIATWO', secretAccessKey: 's2' });

		const sales = await identityFor('sales', location, deps);
		const hr = await identityFor('hr', { bucket: 'data', prefix: 'ducklake/hr/' }, deps);

		expect([sales.accessKeyId, hr.accessKeyId]).toEqual(['AKIAONE', 'AKIATWO']);
		expect(deps.iam.putPolicy).toHaveBeenLastCalledWith('sig_hr', readPolicy('data', 'ducklake/hr/'));
	});
});
