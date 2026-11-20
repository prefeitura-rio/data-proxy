import * as v from 'valibot';

/** The word that names a card of conditions in the editor and in its messages. */
export const GROUP = 'Group';

export type Rule = { column: string; unit_type: string };
export type Editor = { any: Rule[]; all: Rule[][] };
/** A group as the database stores it: one condition, or `all` of two or more. No number exists. */
export type Stored = Rule | { all: Rule[] };

const key = (rule: Rule) => `${rule.column}\u0000${rule.unit_type}`;

const isGroup = (group: Stored): group is { all: Rule[] } => 'all' in group;

/** Split the groups of a table into the alternatives and the cards. A group of one condition is an alternative. */
export function toEditor(groups: Stored[]): Editor {
	const any: Rule[] = [];
	const all: Rule[][] = [];

	for (const group of groups) {
		if (!isGroup(group)) {
			any.push({ column: group.column, unit_type: group.unit_type });
		} else if (group.all.length === 1) {
			any.push(...group.all);
		} else {
			all.push(group.all.map((rule) => ({ column: rule.column, unit_type: rule.unit_type })));
		}
	}

	return { any, all };
}

/** Write the groups to store. A card of one condition is a plain condition, and an empty card is dropped. */
export function toRows(editor: Editor): Stored[] {
	const groups: Stored[] = editor.any.map((rule) => ({ column: rule.column, unit_type: rule.unit_type }));

	for (const card of editor.all) {
		const [only, ...rest] = card;

		if (only === undefined) {
			continue;
		}

		groups.push(
			rest.length === 0
				? { column: only.column, unit_type: only.unit_type }
				: { all: card.map((rule) => ({ column: rule.column, unit_type: rule.unit_type })) }
		);
	}

	return groups;
}

const condition = (rule: Rule) => `its ${rule.column} matches one of their ${rule.unit_type} units`;

const joined = (items: string[]) =>
	items.length < 2 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;

/** Write what the rules mean, in plain words, for the person who edits them. */
export function describe(editor: Editor): string {
	const groups = [
		...editor.any.map((rule) => condition(rule)),
		...editor.all.filter((card) => card.length > 0).map((card) => joined(card.map(condition)))
	];

	if (groups.length === 0) {
		return 'No rule: only a reader of every row sees a row.';
	}

	return `A user sees a row when ${groups.join(', or when ')}.`;
}

/** Flag a card that probably does not do what its author means. */
export function warnings(editor: Editor): string[] {
	const found: string[] = [];

	editor.all.forEach((card, index) => {
		const name = `${GROUP} ${index + 1}`;
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
	group: v.pipe(v.string(), v.regex(/^\d+$/))
});

export type Parsed = { ok: true; rows: Stored[] } | { ok: false; message: string };

/**
 * Read the rules of the editor from the form. Each condition sends three fields in the same order: `rule_column`,
 * `rule_unit_type` and `rule_group`. A group of 0 is an alternative. Conditions with the same way number make one card.
 */
export function parseRules(form: FormData): Parsed {
	const columns = form.getAll('rule_column');
	const types = form.getAll('rule_unit_type');
	const groups = form.getAll('rule_group');

	if (columns.length !== types.length || columns.length !== groups.length) {
		return { ok: false, message: 'The rules are incomplete. Reload the page and try again.' };
	}

	const editor: Editor = { any: [], all: [] };
	const cards = new Map<string, Rule[]>();
	const seen = new Set<string>();

	for (let index = 0; index < columns.length; index += 1) {
		const entry = v.safeParse(Entry, { column: columns[index], unit_type: types[index], group: groups[index] });

		if (!entry.success) {
			const groupProblem = entry.issues.some((issue) => issue.path?.some((part) => part.key === 'group'));

			return {
				ok: false,
				message: groupProblem
					? 'A condition has a wrong group.'
					: 'Fill the column and the unit type of every condition.'
			};
		}

		const rule = { column: entry.output.column, unit_type: entry.output.unit_type };

		if (seen.has(key(rule))) {
			return { ok: false, message: `The rule ${rule.column} with the unit type ${rule.unit_type} appears twice.` };
		}

		seen.add(key(rule));

		if (entry.output.group === '0') {
			editor.any.push(rule);
		} else {
			const card = cards.get(entry.output.group) ?? [];

			card.push(rule);
			cards.set(entry.output.group, card);
		}
	}

	editor.all = [...cards.values()];

	return { ok: true, rows: toRows(editor) };
}

export type Item = Rule & { id: string; card: string | null };
export type Card = { id: string; items: Item[] };
export type Groups = { any: Item[]; cards: Card[] };

const blank = (id: string, card: string | null): Item => ({ id, card, column: '', unit_type: '' });

/** Build the state of the editor for the rules of one table. `ids` makes the ids, so a test can fix them. */
export function startGroups(rows: Stored[], ids: () => string): Groups {
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
export const asEditor = (groups: Groups): Editor => ({ any: groups.any, all: groups.cards.map((card) => card.items) });

/** The conditions in the order that the form sends them, with the number of their card, or 0 for an alternative. */
export const fieldsOf = (groups: Groups): { entry: Item; group: number }[] => [
	...groups.any.map((entry) => ({ entry, group: 0 })),
	...groups.cards.flatMap((card, index) => card.items.map((entry) => ({ entry, group: index + 1 })))
];

/** Remove a condition. A card that has no condition left goes away. */
export function without(groups: Groups, id: string): Groups {
	return {
		any: groups.any.filter((entry) => entry.id !== id),
		cards: groups.cards
			.map((card) => ({ ...card, items: card.items.filter((entry) => entry.id !== id) }))
			.filter((card) => card.items.length > 0)
	};
}

/** Add an empty condition to the alternatives, or to a card. */
export function withBlank(groups: Groups, id: string, card: string | null): Groups {
	if (card === null) {
		return { ...groups, any: [...groups.any, blank(id, null)] };
	}

	return {
		...groups,
		cards: groups.cards.map((found) =>
			found.id === card ? { ...found, items: [...found.items, blank(id, card)] } : found
		)
	};
}

/** Add a card with two empty conditions, because a card of one condition is only an alternative. */
export const withCard = (groups: Groups, card: string, first: string, second: string): Groups => ({
	...groups,
	cards: [...groups.cards, { id: card, items: [blank(first, card), blank(second, card)] }]
});

/** Move a condition into a card that exists. */
export function intoCard(groups: Groups, id: string, card: string): Groups {
	const entry = [...groups.any, ...groups.cards.flatMap((found) => found.items)].find((found) => found.id === id);

	if (entry === undefined || !groups.cards.some((found) => found.id === card)) {
		return groups;
	}

	const rest = without(groups, id);

	return {
		...rest,
		cards: rest.cards.map((found) =>
			found.id === card ? { ...found, items: [...found.items, { ...entry, card }] } : found
		)
	};
}

/** Move a condition out of its card, into the alternatives. */
export function intoAlternatives(groups: Groups, id: string): Groups {
	const entry = groups.cards.flatMap((found) => found.items).find((found) => found.id === id);

	if (entry === undefined) {
		return groups;
	}

	const rest = without(groups, id);

	return { ...rest, any: [...rest.any, { ...entry, card: null }] };
}

/** Move a condition into a new card, with an empty condition beside it. */
export function intoNewCard(groups: Groups, id: string, card: string, second: string): Groups {
	const entry = [...groups.any, ...groups.cards.flatMap((found) => found.items)].find((found) => found.id === id);

	if (entry === undefined) {
		return groups;
	}

	const rest = without(groups, id);

	return { ...rest, cards: [...rest.cards, { id: card, items: [{ ...entry, card }, blank(second, card)] }] };
}
