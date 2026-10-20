import type { Grant } from './messages.ts';

type Files = Grant['tables'][number]['files'];

type View = { registrations: { name: string; url: string }[]; sql: string };

/**
 * Build the registrations and the view of one table from its files.
 * A data file with a delete file becomes an anti join that drops the deleted rows.
 */
export function viewSql(table: string, files: Files): View {
	const registrations: View['registrations'] = [];
	const parts: string[] = [];

	for (const [index, file] of files.entries()) {
		const data = `${table}.d${index}.parquet`;
		registrations.push({ name: data, url: file.data });

		if (file.delete === null) {
			parts.push(`SELECT * FROM read_parquet('${data}')`);
			continue;
		}

		const deleted = `${table}.x${index}.parquet`;
		registrations.push({ name: deleted, url: file.delete });
		parts.push(
			`SELECT d.* EXCLUDE (file_row_number) FROM read_parquet('${data}', file_row_number = true) d ` +
				`ANTI JOIN read_parquet('${deleted}') x ON d.file_row_number = x.pos`
		);
	}

	const quoted = `"${table.replaceAll('"', '""')}"`;

	return { registrations, sql: `CREATE OR REPLACE VIEW ${quoted} AS ${parts.join(' UNION ALL ')}` };
}
