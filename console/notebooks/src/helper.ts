import * as duckdb from '@duckdb/duckdb-wasm';
import * as v from 'valibot';
import { type Grant, GrantMessage, type Status } from './messages.ts';
import { viewSql } from './view.ts';

/**
 * Start DuckDB-WASM in this worker and wait for grants from the console.
 * The page script passes each grant over the BroadcastChannel named grant.
 * The extension for Parquet comes from this site, not from the internet.
 */
export async function start(): Promise<void> {
	if (globalThis.dataProxy) {
		return;
	}

	const origin = self.location.origin;
	const worker = new Worker(`${origin}/duckdb/duckdb-browser-eh.worker.js`);
	const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), worker);
	await db.instantiate(`${origin}/duckdb/duckdb-eh.wasm`);
	const connection = await db.connect();
	await connection.query(`SET custom_extension_repository = '${origin}/duckdb/ext'`);

	const status = new BroadcastChannel('status');
	const tell = (message: Status): void => {
		status.postMessage(message);
	};
	const registered = new Set<string>();
	const first = Promise.withResolvers<undefined>();
	let applied = 0;

	async function apply(grant: Grant): Promise<void> {
		for (const table of grant.tables) {
			const { registrations, sql } = viewSql(table.name, table.files);

			for (const { name, url } of registrations) {
				if (registered.has(name)) {
					await db.dropFile(name);
				}

				await db.registerFileURL(name, url, duckdb.DuckDBDataProtocol.HTTP, false);
				registered.add(name);
			}

			await connection.query(sql);
		}

		applied += 1;
		tell({ type: 'applied', schema: grant.schema, count: applied, expiresAt: grant.expiresAt });
		first.resolve(undefined);
	}

	new BroadcastChannel('grant').onmessage = (event: MessageEvent<unknown>) => {
		const message = v.safeParse(GrantMessage, event.data);

		if (message.success) {
			apply(message.output.grant).catch((error: unknown) => {
				tell({ type: 'error', message: String(error) });
			});
		}
	};

	globalThis.dataProxy = {
		first: first.promise,
		query: async (sql, limit) => {
			if (!Number.isInteger(limit)) {
				throw new Error('The limit must be a whole number');
			}

			const result = await connection.query(`SELECT * FROM (${sql}) LIMIT ${limit}`);

			return JSON.stringify(result.toArray(), (_key, value: unknown) =>
				typeof value === 'bigint' ? Number(value) : value
			);
		}
	};

	self.addEventListener('securitypolicyviolation', (event) => {
		tell({ type: 'csp', directive: event.violatedDirective, blocked: event.blockedURI });
	});
	tell({ type: 'ready' });
}
