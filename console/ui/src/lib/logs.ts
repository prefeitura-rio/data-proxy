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
