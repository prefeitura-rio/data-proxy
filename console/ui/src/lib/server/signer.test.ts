import { describe, expect, it } from 'vitest';
import { signWith, type SignerConfig } from './signer';

const config: SignerConfig = {
	endpoint: 'http://storage:8333',
	region: 'us-east-1',
	bucket: 'data',
	fallback: { accessKeyId: 'shared', secretAccessKey: 'shared-secret' }
};

describe('signWith', () => {
	it('signs a GET link that expires in five minutes with the given credential', async () => {
		const sign = signWith(config, { accessKeyId: 'sales-key', secretAccessKey: 'sales-secret' });
		const url = new URL(await sign('ducklake/sales/main/people/a.parquet'));

		expect(url.origin + url.pathname).toBe('http://storage:8333/data/ducklake/sales/main/people/a.parquet');
		expect(url.searchParams.get('X-Amz-Expires')).toBe('300');
		expect(url.searchParams.get('X-Amz-Credential')).toContain('sales-key/');
		expect(url.searchParams.get('X-Amz-SignedHeaders')).toBe('host');
	});

	it('signs a different link for another key', async () => {
		const sign = signWith(config, config.fallback);

		expect(await sign('a')).not.toBe(await sign('b'));
	});
});
