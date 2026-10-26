import type { LogLine } from './types';

export function mergeLogs(known: LogLine[], fresh: LogLine[]): LogLine[] {
	const seen = new Set(known.map((line) => `${line.workflow_id}:${line.position}`));
	const added = fresh.filter((line) => {
		const id = `${line.workflow_id}:${line.position}`;

		if (seen.has(id)) {
			return false;
		}

		seen.add(id);
		return true;
	});

	return [...known, ...added].sort(
		(a, b) =>
			a.logged_at.localeCompare(b.logged_at) || a.workflow_id.localeCompare(b.workflow_id) || a.position - b.position
	);
}

export const lastLoggedAt = (lines: LogLine[]) =>
	lines
		.map((line) => line.logged_at)
		.sort()
		.at(-1) ?? null;

type Page<T> = { rows: T[]; total: number | null };

/**
 * Read the first `wanted` rows through a source that may return fewer rows than it was asked for, because PostgREST
 * caps a response at its maximum of rows. Ask again from the end of the rows that arrived, until there are enough.
 */
export async function collectLines<T>(
	fetchPage: (limit: number, offset: number) => Promise<Page<T>>,
	wanted: number
): Promise<Page<T>> {
	const rows: T[] = [];
	let total: number | null = null;

	while (rows.length < wanted && (total === null || rows.length < total)) {
		const page = await fetchPage(wanted - rows.length, rows.length);

		total = page.total;

		if (page.rows.length === 0) {
			break;
		}

		rows.push(...page.rows);
	}

	return { rows, total };
}

export type LogFilter = { level: string; table: string; text: string };

const valueText = (value: unknown): string => (typeof value === 'string' ? value : JSON.stringify(value));

/** The pieces of one line: the time, the level, the schema and the table, and the message with its fields as `key=value`. */
export function lineParts(line: LogLine) {
	const context = [line.schema_name, line.table_name].filter((part) => part !== null && part !== '').join('.');
	const pairs = Object.entries(line.fields ?? {})
		.map(([name, value]) => `${name}=${valueText(value)}`)
		.join(' ');

	return {
		time: line.logged_at,
		level: line.level,
		context,
		message: pairs === '' ? line.message : `${line.message}: ${pairs}`
	};
}

/** The text of one line: its pieces in a row, without the empty ones. */
export const lineText = (line: LogLine): string =>
	Object.values(lineParts(line))
		.filter((part) => part !== '')
		.join(' ');

/** Keep the lines of one level and one table, or of every one when the filter is empty, whose text contains the text. */
export function filterLogs(lines: LogLine[], { level, table, text }: LogFilter): LogLine[] {
	const needle = text.trim().toLowerCase();

	return lines.filter(
		(line) =>
			(level === '' || line.level === level) &&
			(table === '' || line.table_name === table) &&
			(needle === '' || lineText(line).toLowerCase().includes(needle))
	);
}

export const levelsOf = (lines: LogLine[]): string[] => [...new Set(lines.map((line) => line.level))].sort();

/** The tables that the lines name, each once, in order. */
export const tablesOf = (lines: LogLine[]): string[] =>
	[
		...new Set(lines.flatMap((line) => (line.table_name === null || line.table_name === '' ? [] : [line.table_name])))
	].sort();

/** The text of the lines, as the page shows them. */
export const logText = (lines: LogLine[]): string => lines.map(lineText).join('\n');

/** The reader follows the end of the log when the box is scrolled to its bottom, within a small slack. */
export const atBottom = (box: { scrollTop: number; clientHeight: number; scrollHeight: number }, slack = 24): boolean =>
	box.scrollHeight - box.scrollTop - box.clientHeight <= slack;

/** The text color of a log level. */
export function levelTone(level: string): string {
	switch (level.toUpperCase()) {
		case 'ERROR':
		case 'CRITICAL':
			return 'text-error';
		case 'WARNING':
			return 'text-warning';
		case 'DEBUG':
			return 'text-base-content/50';
		default:
			return '';
	}
}
