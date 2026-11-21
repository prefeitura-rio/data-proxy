export const MAX_UNITS = 100;

export type Unit = { unit_type: string; unit_id: string };
export type Parsed = { ok: true; rows: Unit[] } | { ok: false; message: string };

const WILDCARD = '*';

/**
 * Read the unit ids of a grant: one id, a list of ids, or the single pair `*` and `*`. Never a mix.
 * A list takes a comma, with or without a space after it, or a new line, between two ids.
 */
export function parseUnits(unitType: string, unitIdText: string): Parsed {
	const type = unitType.trim();
	const ids = [
		...new Set(
			unitIdText
				.split(/[,\r\n]/)
				.map((id) => id.trim())
				.filter(Boolean)
		)
	];

	if (type === '' || ids.length === 0) {
		return { ok: false, message: 'Enter a unit type and at least one unit id.' };
	}

	if (ids.length > MAX_UNITS) {
		return { ok: false, message: `At most ${MAX_UNITS} ids at once.` };
	}

	const wildcard = ids.includes(WILDCARD);

	if (wildcard && ids.length > 1) {
		return { ok: false, message: 'Use * alone, not with other ids.' };
	}

	if (wildcard && type !== WILDCARD) {
		return { ok: false, message: 'The id * needs the unit type *.' };
	}

	if (!wildcard && type === WILDCARD) {
		return { ok: false, message: 'The unit type * needs the id *.' };
	}

	return { ok: true, rows: ids.map((unit_id) => ({ unit_type: type, unit_id })) };
}

/** The scopes that cover a path of the catalog: the empty scope and each prefix of the path. */
export const scopesOf = (path: string) => {
	const labels = path.split('.');

	return ['', ...labels.map((_, index) => labels.slice(0, index + 1).join('.'))];
};

/** The path of a table in the rows of the catalog of its schema, or the path of the schema if the table has none. */
export const explainPath = (entries: { table_name: string | null; path: string | null }[], table: string | null) => {
	const name = table?.split('.').pop();
	const own = name ? entries.find((entry) => entry.table_name === name)?.path : null;

	return own ?? entries.find((entry) => entry.table_name === null)?.path ?? null;
};
