import { describe, expect, it } from 'vitest';
import { TTL_SECONDS } from './grant';
import { queryView } from './query-view';
import type { QuerySettings } from './settings';

const settings = (ttlSeconds: number): QuerySettings => ({
	notebookOrigin: 'https://notebooks.example.test',
	catalogUrl: 'postgresql://catalog',
	signer: {
		endpoint: 'https://storage.example.test',
		region: 'us-east-1',
		bucket: 'data',
		ttlSeconds,
		fallback: { accessKeyId: 'key', secretAccessKey: 'secret' }
	},
	iam: null,
	keysSecret: 'secret'
});

describe('queryView', () => {
	it.each([
		{ name: 'a setting is missing', given: null, notebookOrigin: null, grantTtlSeconds: TTL_SECONDS },
		{
			name: 'every setting is there',
			given: settings(30),
			notebookOrigin: 'https://notebooks.example.test',
			grantTtlSeconds: 30
		}
	])('returns the notebook and the link lifetime when $name', ({ given, notebookOrigin, grantTtlSeconds }) => {
		expect(queryView(given)).toEqual({ notebookOrigin, grantTtlSeconds });
	});
});
