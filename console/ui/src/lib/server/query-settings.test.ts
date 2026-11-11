import { describe, expect, it } from 'vitest';
import { querySettingsFrom, type QueryEnv } from './query-settings';

const complete: QueryEnv = {
	NOTEBOOK_ORIGIN: 'https://notebooks.example.test',
	CATALOG_URL: 'postgresql://catalog.example.test/ducklake',
	S3_ENDPOINT: 'https://s3.example.test',
	S3_BUCKET: 'bucket',
	S3_ACCESS_KEY: 'reader',
	S3_SECRET_KEY: 'reader-secret',
	COOKIE_SECRET: 'cookie',
	ORIGIN: 'https://console.example.test'
};

describe('querySettingsFrom', () => {
	it.each(['NOTEBOOK_ORIGIN', 'CATALOG_URL', 'S3_ENDPOINT', 'S3_BUCKET', 'S3_ACCESS_KEY', 'S3_SECRET_KEY'] as const)(
		'returns nothing when %s is missing, so the other tabs still work',
		(name) => {
			expect(querySettingsFrom({ ...complete, [name]: undefined })).toBeNull();
			expect(querySettingsFrom({ ...complete, [name]: '' })).toBeNull();
		}
	);

	it('fills the region and the lifetime of the links when the environment sets neither', () => {
		const settings = querySettingsFrom(complete);

		expect(settings?.signer.region).toBe('us-east-1');
		expect(settings?.signer.ttlSeconds).toBe(300);
		expect(settings?.iam).toBeNull();
	});

	it('reads the region, the lifetime and the reader credential', () => {
		const settings = querySettingsFrom({ ...complete, S3_REGION: 'sa-east-1', GRANT_TTL_SECONDS: '30' });

		expect(settings?.signer).toMatchObject({
			region: 'sa-east-1',
			ttlSeconds: 30,
			fallback: { accessKeyId: 'reader', secretAccessKey: 'reader-secret' }
		});
	});

	it('refuses a lifetime that is not a positive whole number', () => {
		expect(() => querySettingsFrom({ ...complete, GRANT_TTL_SECONDS: '0' })).toThrow('GRANT_TTL_SECONDS');
	});

	it('turns the IAM admin on only when both of its keys are set, and the endpoint falls back to the storage', () => {
		const admin = { S3_ADMIN_ACCESS_KEY: 'admin', S3_ADMIN_SECRET_KEY: 'admin-secret' };

		expect(querySettingsFrom({ ...complete, S3_ADMIN_ACCESS_KEY: 'admin' })?.iam).toBeNull();
		expect(querySettingsFrom({ ...complete, ...admin })?.iam).toMatchObject({
			endpoint: 'https://s3.example.test',
			accessKeyId: 'admin'
		});
		expect(
			querySettingsFrom({ ...complete, ...admin, S3_IAM_ENDPOINT: 'https://iam.example.test' })?.iam?.endpoint
		).toBe('https://iam.example.test');
	});

	it.each([
		['the same origin', 'https://console.example.test'],
		['the same origin with a path', 'https://console.example.test/notebooks'],
		['the same origin with a slash', 'https://console.example.test/']
	])('refuses a notebook on %s as the console, because the sandbox would not separate them', (_, notebook) => {
		expect(() => querySettingsFrom({ ...complete, NOTEBOOK_ORIGIN: notebook })).toThrow('another origin than ORIGIN');
	});

	it.each([
		['another host', 'https://notebooks.example.test'],
		['another port', 'https://console.example.test:8443'],
		['another scheme', 'http://console.example.test']
	])('accepts a notebook on %s', (_, notebook) => {
		expect(querySettingsFrom({ ...complete, NOTEBOOK_ORIGIN: notebook })?.notebookOrigin).toBe(notebook);
	});

	it('derives the secret that seals the keys from the secret of the cookie when KEYS_SECRET is not set', () => {
		expect(querySettingsFrom(complete)?.keysSecret).toBe('cookie:signing-keys');
	});

	it('seals the keys with KEYS_SECRET when it is set, so a new cookie secret leaves the keys alone', () => {
		const sealed = (cookie: string) => querySettingsFrom({ ...complete, COOKIE_SECRET: cookie, KEYS_SECRET: 'own' });

		expect(sealed('cookie')?.keysSecret).toBe('own');
		expect(sealed('rotated')?.keysSecret).toBe('own');
	});
});
