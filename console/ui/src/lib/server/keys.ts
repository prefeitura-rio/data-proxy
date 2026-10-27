import pg from 'pg';
import type { KeyStore } from './identities';

/** Keep the sealed keys in the table signer.keys, and lock a schema so that two replicas make one key. */
export function catalogKeys(catalogUrl: string): KeyStore {
	return {
		async get(schema) {
			const client = new pg.Client({ connectionString: catalogUrl });
			await client.connect();

			try {
				const result = await client.query<{ access_key: string; secret_sealed: string }>(
					'SELECT access_key, secret_sealed FROM signer.keys WHERE schema_name = $1',
					[schema]
				);
				const row = result.rows[0];

				return row ? { accessKeyId: row.access_key, sealed: row.secret_sealed } : null;
			} finally {
				await client.end();
			}
		},
		async put(schema, accessKeyId, sealed) {
			const client = new pg.Client({ connectionString: catalogUrl });
			await client.connect();

			try {
				await client.query(
					`INSERT INTO signer.keys (schema_name, access_key, secret_sealed) VALUES ($1, $2, $3)
					ON CONFLICT (schema_name) DO UPDATE SET access_key = $2, secret_sealed = $3, created_at = now()`,
					[schema, accessKeyId, sealed]
				);
			} finally {
				await client.end();
			}
		},
		async locked(schema, work) {
			const client = new pg.Client({ connectionString: catalogUrl });
			await client.connect();

			try {
				await client.query('SELECT pg_advisory_lock(hashtext($1))', [`signer:${schema}`]);

				try {
					return await work();
				} finally {
					await client.query('SELECT pg_advisory_unlock(hashtext($1))', [`signer:${schema}`]);
				}
			} finally {
				await client.end();
			}
		}
	};
}
