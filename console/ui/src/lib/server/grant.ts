import { destination } from '#lib/tables.js';
import type { Grant } from '#lib/types.js';
import type { Result } from './postgrest';

export const TTL_SECONDS = 300;

/** The lifetime of the links in seconds: the value of GRANT_TTL_SECONDS, or five minutes without one. */
export function ttlFrom(raw: string | undefined): number {
	if (raw === undefined || raw === '') {
		return TTL_SECONDS;
	}

	const seconds = Number(raw);

	if (!Number.isInteger(seconds) || seconds < 1) {
		throw new Error('GRANT_TTL_SECONDS must be a positive whole number of seconds');
	}

	return seconds;
}

const SCHEMA_NAME = /^[a-z][a-z0-9_]{0,55}$/;

export type FileRow = {
	table: string;
	schemaPath: string;
	schemaRelative: boolean;
	tablePath: string;
	tableRelative: boolean;
	filePath: string;
	fileRelative: boolean;
	deletePath: string | null;
	deleteRelative: boolean;
};

export function resolvePath(dataPath: string, parts: [string, boolean][]): string {
	let resolved = dataPath;

	for (const [path, relative] of parts) {
		resolved = relative ? resolved.replace(/\/?$/, '/') + path : path;
	}

	if (resolved.split('/').includes('..')) {
		throw new Error(`The path ${resolved} is outside the data path`);
	}

	return resolved;
}

function keyOf(path: string, bucket: string): string {
	const prefix = `s3://${bucket}/`;

	if (!path.startsWith(prefix)) {
		throw new Error(`The path ${path} is not in the bucket ${bucket}`);
	}

	return path.slice(prefix.length);
}

export async function buildGrant(options: {
	schema: string;
	dataPath: string;
	bucket: string;
	rows: FileRow[];
	sign: (key: string) => Promise<string> | string;
	now: number;
	ttlSeconds?: number;
}): Promise<Grant> {
	const tables = new Map<string, Grant['tables'][number]>();

	for (const row of options.rows) {
		const base: [string, boolean][] = [
			[row.schemaPath, row.schemaRelative],
			[row.tablePath, row.tableRelative]
		];
		const data = resolvePath(options.dataPath, [...base, [row.filePath, row.fileRelative]]);
		const deleted =
			row.deletePath === null ? null : resolvePath(options.dataPath, [...base, [row.deletePath, row.deleteRelative]]);
		const table = tables.get(row.table) ?? { name: row.table, files: [] };

		table.files.push({
			data: await options.sign(keyOf(data, options.bucket)),
			delete: deleted === null ? null : await options.sign(keyOf(deleted, options.bucket))
		});
		tables.set(row.table, table);
	}

	return {
		schema: options.schema,
		tables: [...tables.values()],
		expiresAt: options.now + (options.ttlSeconds ?? TTL_SECONDS) * 1000
	};
}

/** The key prefix of a data path, with the trailing slash, for the policy of an identity. */
export function prefixOf(dataPath: string, bucket: string): string {
	return keyOf(dataPath.replace(/\/?$/, '/'), bucket);
}

type Deps = {
	authorize: (schema: string, table: string) => Promise<Result<unknown>>;
	list: (schema: string, tables: string[]) => Promise<{ dataPath: string; rows: FileRow[] }>;
	/** Make the function that signs for one schema, once its data path is known. */
	signerFor: (dataPath: string) => Promise<(key: string) => Promise<string> | string>;
	bucket: string;
	now: number;
	ttlSeconds?: number;
};

/** Authorize each table with the token of the user, then list and sign only what that allows. */
export async function createGrant(schema: string, tables: string[], deps: Deps): Promise<Result<Grant>> {
	if (!SCHEMA_NAME.test(schema) || tables.length === 0) {
		return { ok: false, status: 400, message: 'The value is wrong: choose a schema and at least one table' };
	}

	for (const table of tables) {
		const result = await deps.authorize(schema, table);

		if (!result.ok) {
			return result;
		}
	}

	const { dataPath, rows } = await deps.list(schema, tables.map(destination));

	return {
		ok: true,
		data: await buildGrant({
			schema,
			dataPath,
			bucket: deps.bucket,
			rows,
			sign: await deps.signerFor(dataPath),
			now: deps.now,
			ttlSeconds: deps.ttlSeconds
		})
	};
}
