const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;

/** Say in one sentence what an import would do, from the rows that the preview returns. */
export function summaryText(changes: { action: string }[]): string {
	if (changes.length === 0) {
		return 'The document holds nothing to import.';
	}

	const count = (action: string) => changes.filter((change) => change.action === action).length;
	const [inserts = 0, updates = 0, deletes = 0, same = 0] = ['insert', 'update', 'delete', 'unchanged'].map(count);

	if (inserts + updates + deletes === 0) {
		return `Nothing changes: ${same} ${same === 1 ? 'row is' : 'rows are'} the same.`;
	}

	return `${plural(inserts, 'insert')}, ${plural(updates, 'update')}, ${plural(deletes, 'delete')}, ${same} the same.`;
}

/** The badge color of the action of one change of an import. */
export function actionTone(action: string): string {
	switch (action) {
		case 'insert':
			return 'badge-success';
		case 'update':
			return 'badge-warning';
		case 'delete':
			return 'badge-error';
		default:
			return 'badge-neutral';
	}
}
