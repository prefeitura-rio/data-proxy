import * as v from 'valibot';

/** The word that names a card of conditions in the editor and in its messages. */
export const WAY = 'Way';

export type Rule = { column: string; unit_type: string };
export type Editor = { any: Rule[]; all: Rule[][] };
/** A way as the database stores it: one condition, or `all` of two or more. No number exists. */
export type Stored = Rule | { all: Rule[] };

const key = (rule: Rule) => `${rule.column}\u0000${rule.unit_type}`;

const isWay = (way: Stored): way is { all: Rule[] } => 'all' in way;

/** Split the ways of a table into the alternatives and the cards. A way of one condition is an alternative. */
export function toEditor(ways: Stored[]): Editor {
	const any: Rule[] = [];
	const all: Rule[][] = [];

	for (const way of ways) {
		if (!isWay(way)) {
			any.push({ column: way.column, unit_type: way.unit_type });
		} else if (way.all.length === 1) {
			any.push(...way.all);
		} else {
			all.push(way.all.map((rule) => ({ column: rule.column, unit_type: rule.unit_type })));
		}
	}

	return { any, all };
}

/** Write the ways to store. A card of one condition is a plain condition, and an empty card is dropped. */
export function toRows(editor: Editor): Stored[] {
	const ways: Stored[] = editor.any.map((rule) => ({ column: rule.column, unit_type: rule.unit_type }));

	for (const card of editor.all) {
		const [only, ...rest] = card;

		if (only === undefined) {
			continue;
		}

		ways.push(
			rest.length === 0
				? { column: only.column, unit_type: only.unit_type }
				: { all: card.map((rule) => ({ column: rule.column, unit_type: rule.unit_type })) }
		);
	}

	return ways;
}

const condition = (rule: Rule) => `its ${rule.column} matches one of their ${rule.unit_type} units`;

const joined = (items: string[]) =>
	items.length < 2 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;

/** Write what the rules mean, in plain words, for the person who edits them. */
export function describe(editor: Editor): string {
	const ways = [
		...editor.any.map((rule) => condition(rule)),
		...editor.all.filter((card) => card.length > 0).map((card) => joined(card.map(condition)))
	];

	if (ways.length === 0) {
		return 'No rule: only a reader of every row sees a row.';
	}

	return `A user sees a row when ${ways.join(', or when ')}.`;
}

/** Flag a card that probably does not do what its author means. */
export function warnings(editor: Editor): string[] {
	const found: string[] = [];

	editor.all.forEach((card, index) => {
		const name = `${WAY} ${index + 1}`;
		const types = [...new Set(card.map((rule) => rule.unit_type))];

		if (card.length === 1) {
			found.push(`${name} has one condition, so it works like a condition on its own.`);
		}

		if (new Set(card.map(key)).size < card.length) {
			found.push(`${name} holds the same condition twice.`);
		}

		if (types.length > 1) {
			found.push(
				`${name} mixes the unit types ${joined(types)}. A user needs a grant of each type, or sees no row from it.`
			);
		}
	});

	return found;
}

const Entry = v.object({
	column: v.pipe(v.string(), v.trim(), v.minLength(1)),
	unit_type: v.pipe(v.string(), v.trim(), v.minLength(1)),
	way: v.pipe(v.string(), v.regex(/^\d+$/))
});

export type Parsed = { ok: true; rows: Stored[] } | { ok: false; message: string };

/**
 * Read the rules of the editor from the form. Each condition sends three fields in the same order: `rule_column`,
 * `rule_unit_type` and `rule_way`. A way of 0 is an alternative. Conditions with the same way number make one card.
 */
export function parseRules(form: FormData): Parsed {
	const columns = form.getAll('rule_column');
	const types = form.getAll('rule_unit_type');
	const ways = form.getAll('rule_way');

	if (columns.length !== types.length || columns.length !== ways.length) {
		return { ok: false, message: 'The rules are incomplete. Reload the page and try again.' };
	}

	const editor: Editor = { any: [], all: [] };
	const cards = new Map<string, Rule[]>();
	const seen = new Set<string>();

	for (let index = 0; index < columns.length; index += 1) {
		const entry = v.safeParse(Entry, { column: columns[index], unit_type: types[index], way: ways[index] });

		if (!entry.success) {
			const wayProblem = entry.issues.some((issue) => issue.path?.some((part) => part.key === 'way'));

			return {
				ok: false,
				message: wayProblem ? 'A condition has a wrong way.' : 'Fill the column and the unit type of every condition.'
			};
		}

		const rule = { column: entry.output.column, unit_type: entry.output.unit_type };

		if (seen.has(key(rule))) {
			return { ok: false, message: `The rule ${rule.column} with the unit type ${rule.unit_type} appears twice.` };
		}

		seen.add(key(rule));

		if (entry.output.way === '0') {
			editor.any.push(rule);
		} else {
			const card = cards.get(entry.output.way) ?? [];

			card.push(rule);
			cards.set(entry.output.way, card);
		}
	}

	editor.all = [...cards.values()];

	return { ok: true, rows: toRows(editor) };
}

export type Item = Rule & { id: string; card: string | null };
export type Card = { id: string; items: Item[] };
export type Ways = { any: Item[]; cards: Card[] };

const blank = (id: string, card: string | null): Item => ({ id, card, column: '', unit_type: '' });

/** Build the state of the editor for the rules of one table. `ids` makes the ids, so a test can fix them. */
export function startWays(rows: Stored[], ids: () => string): Ways {
	const editor = toEditor(rows);

	return {
		any: editor.any.map((rule) => ({ ...rule, id: ids(), card: null })),
		cards: editor.all.map((rules) => {
			const id = ids();

			return { id, items: rules.map((rule) => ({ ...rule, id: ids(), card: id })) };
		})
	};
}

/** The editor shape of the state, for the sentence and the warnings. */
export const asEditor = (ways: Ways): Editor => ({ any: ways.any, all: ways.cards.map((card) => card.items) });

/** The conditions in the order that the form sends them, with the number of their card, or 0 for an alternative. */
export const fieldsOf = (ways: Ways): { entry: Item; way: number }[] => [
	...ways.any.map((entry) => ({ entry, way: 0 })),
	...ways.cards.flatMap((card, index) => card.items.map((entry) => ({ entry, way: index + 1 })))
];

/** Remove a condition. A card that has no condition left goes away. */
export function without(ways: Ways, id: string): Ways {
	return {
		any: ways.any.filter((entry) => entry.id !== id),
		cards: ways.cards
			.map((card) => ({ ...card, items: card.items.filter((entry) => entry.id !== id) }))
			.filter((card) => card.items.length > 0)
	};
}

/** Add an empty condition to the alternatives, or to a card. */
export function withBlank(ways: Ways, id: string, card: string | null): Ways {
	if (card === null) {
		return { ...ways, any: [...ways.any, blank(id, null)] };
	}

	return {
		...ways,
		cards: ways.cards.map((found) =>
			found.id === card ? { ...found, items: [...found.items, blank(id, card)] } : found
		)
	};
}

/** Add a card with two empty conditions, because a card of one condition is only an alternative. */
export const withCard = (ways: Ways, card: string, first: string, second: string): Ways => ({
	...ways,
	cards: [...ways.cards, { id: card, items: [blank(first, card), blank(second, card)] }]
});

/** Move a condition into a card that exists. */
export function intoCard(ways: Ways, id: string, card: string): Ways {
	const entry = [...ways.any, ...ways.cards.flatMap((found) => found.items)].find((found) => found.id === id);

	if (entry === undefined || !ways.cards.some((found) => found.id === card)) {
		return ways;
	}

	const rest = without(ways, id);

	return {
		...rest,
		cards: rest.cards.map((found) =>
			found.id === card ? { ...found, items: [...found.items, { ...entry, card }] } : found
		)
	};
}

/** Move a condition out of its card, into the alternatives. */
export function intoAlternatives(ways: Ways, id: string): Ways {
	const entry = ways.cards.flatMap((found) => found.items).find((found) => found.id === id);

	if (entry === undefined) {
		return ways;
	}

	const rest = without(ways, id);

	return { ...rest, any: [...rest.any, { ...entry, card: null }] };
}

/** Move a condition into a new card, with an empty condition beside it. */
export function intoNewCard(ways: Ways, id: string, card: string, second: string): Ways {
	const entry = [...ways.any, ...ways.cards.flatMap((found) => found.items)].find((found) => found.id === id);

	if (entry === undefined) {
		return ways;
	}

	const rest = without(ways, id);

	return { ...rest, cards: [...rest.cards, { id: card, items: [{ ...entry, card }, blank(second, card)] }] };
}

export type Grant = { unit_type: string | null; unit_id: string | null };
export type Found = { column: string; unit_type: string; values: string[] };
export type CardResult = { active: boolean; missing: string[]; conditions: Found[] };
export type Explained = { everything: boolean; any: Found[]; cards: CardResult[]; rows: string };

const WILDCARD = '*';

const valuesOf = (grants: Grant[], unitType: string) => [
	...new Set(
		grants.filter((grant) => grant.unit_type === unitType && grant.unit_id !== null).map((grant) => grant.unit_id ?? '')
	)
];

const isOne = (found: Found) => found.values.length > 0;

const clause = (found: Found) => `its ${found.column} is ${found.values.join(' or ')}`;

/**
 * Say what the rules give to a subject that holds these grants. A card with a rule that has no grant drops out, as the
 * database does. The grant `*` on `*` gives every row. It shows no row count, because a count needs a read of the data.
 */
export function explain(editor: Editor, grants: Grant[]): Explained {
	const find = (rule: Rule): Found => ({
		column: rule.column,
		unit_type: rule.unit_type,
		values: valuesOf(grants, rule.unit_type)
	});
	const any = editor.any.map(find);
	const cards = editor.all
		.filter((card) => card.length > 0)
		.map((card): CardResult => {
			const conditions = card.map(find);
			const missing = conditions.filter((found) => !isOne(found)).map((found) => found.unit_type);

			return { active: missing.length === 0, missing, conditions };
		});
	const everything = grants.some((grant) => grant.unit_type === WILDCARD && grant.unit_id === WILDCARD);

	if (everything) {
		return { everything, any, cards, rows: 'The subject sees every row, because it holds the grant * on * .' };
	}

	if (any.length === 0 && cards.length === 0) {
		return { everything, any, cards, rows: 'No rule, so the subject sees no row.' };
	}

	const ways = [
		...any.filter(isOne).map(clause),
		...cards.filter((card) => card.active).map((card) => joined(card.conditions.map(clause)))
	];

	return {
		everything,
		any,
		cards,
		rows:
			ways.length === 0
				? 'The subject sees no row, because no condition has a grant.'
				: `The subject sees a row when ${ways.join(', or when ')}.`
	};
}
