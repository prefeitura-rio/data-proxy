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
