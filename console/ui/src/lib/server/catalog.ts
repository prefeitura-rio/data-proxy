import pg from 'pg';
import type { FileRow } from './grant';

/** A row of the DuckLake catalog: a data file of a table, with the delete file that hides some of its rows. */
export type CatalogRow = {
	table_name: string;
	schema_path: string;
	schema_relative: boolean;
	table_path: string;
	table_relative: boolean;
	file_path: string;
	file_relative: boolean;
	delete_path: string | null;
	delete_relative: boolean | null;
};

/** Turn the rows of the catalog into the rows of a grant. A table with no delete file is not relative to one. */
export function rowsToFiles(rows: CatalogRow[]): FileRow[] {
	return rows.map((row) => ({
		table: row.table_name,
		schemaPath: row.schema_path,
		schemaRelative: row.schema_relative,
		tablePath: row.table_path,
		tableRelative: row.table_relative,
		filePath: row.file_path,
		fileRelative: row.file_relative,
		deletePath: row.delete_path,
		deleteRelative: row.delete_relative ?? false
	}));
}

/** List the data files and the delete files of tables at the latest snapshot, as the role of one schema. */
export async function listFiles(
	catalogUrl: string,
	schema: string,
	tables: string[]
): Promise<{ dataPath: string; rows: FileRow[] }> {
	const client = new pg.Client({ connectionString: catalogUrl });
	await client.connect();

	try {
		await client.query('BEGIN READ ONLY');
		await client.query(`SET LOCAL ROLE ${pg.escapeIdentifier(`schema_${schema}`)}`);

		const meta = pg.escapeIdentifier(schema);
		const location = await client.query<{ value: string }>(
			`SELECT value FROM ${meta}.ducklake_metadata WHERE key = 'data_path' AND scope IS NULL`
		);
		const files = await client.query<CatalogRow>(
			`SELECT t.table_name, s.path AS schema_path, s.path_is_relative AS schema_relative,
				t.path AS table_path, t.path_is_relative AS table_relative,
				f.path AS file_path, f.path_is_relative AS file_relative,
				d.path AS delete_path, d.path_is_relative AS delete_relative
			FROM ${meta}.ducklake_table AS t
			INNER JOIN ${meta}.ducklake_schema AS s ON s.schema_id = t.schema_id AND s.end_snapshot IS NULL
			INNER JOIN ${meta}.ducklake_data_file AS f ON f.table_id = t.table_id AND f.end_snapshot IS NULL
			LEFT JOIN ${meta}.ducklake_delete_file AS d ON d.data_file_id = f.data_file_id AND d.end_snapshot IS NULL
			WHERE t.end_snapshot IS NULL AND s.schema_name = 'main' AND t.table_name = ANY($1)
			ORDER BY t.table_name, f.file_order, f.data_file_id`,
			[tables]
		);

		await client.query('COMMIT');

		const dataPath = location.rows[0]?.value;

		if (!dataPath) {
			throw new Error(`The catalog of ${schema} has no data path`);
		}

		return { dataPath, rows: rowsToFiles(files.rows) };
	} finally {
		await client.end();
	}
}
