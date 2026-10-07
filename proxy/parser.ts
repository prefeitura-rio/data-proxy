import generatedParser from './peggy/filter.generated.js';

interface GeneratedComparison {
    kind: 'comparison';
    column: string;
    operator: string;
    negated: boolean;
    value: string | string[];
}

interface GeneratedGroup {
    kind: 'group';
    operator: 'and' | 'or';
    negated?: boolean;
    items: GeneratedNode[];
}

type GeneratedNode = GeneratedComparison | GeneratedGroup;

export interface ComparisonNode {
    kind: 'comparison';
    column: string;
    operator: FilterOperator;
    negated: boolean;
    value: string | string[];
}

export interface GroupNode {
    kind: 'group';
    operator: 'and' | 'or';
    negated?: boolean;
    items: FilterNode[];
}

export type FilterNode = ComparisonNode | GroupNode;

export interface OrderNode {
    column: string;
    direction: 'asc' | 'desc';
    nulls?: 'first' | 'last';
}

export interface DuckLakeFilter {
    where?: FilterNode;
    order?: OrderNode[];
    limit?: number;
    offset?: number;
}

type FilterOperator =
    | 'eq'
    | 'neq'
    | 'gt'
    | 'gte'
    | 'lt'
    | 'lte'
    | 'in'
    | 'is'
    | 'like'
    | 'ilike';

type Modifier = 'order' | 'limit' | 'offset';

const OPERATOR_PREFIX = /^(?:not\.)?(eq|neq|gt|gte|lt|lte|in|is|like|ilike)(?:\.|$)/;
const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]*$/;
const NONNEGATIVE_INTEGER = /^(0|[1-9][0-9]*)$/;

function decode(value: string): string | null {
    try {
        return decodeURIComponent(value);
    } catch (error) {
        return null;
    }
}

function nodeFromGenerated(node: GeneratedNode): FilterNode {
    if (node.kind === 'group') {
        const group: GroupNode = {
            kind: 'group',
            operator: node.operator,
            items: node.items.map(nodeFromGenerated),
        };

        if (node.negated) {
            group.negated = true;
        }

        return group;
    }

    if (node.operator === 'in' && !Array.isArray(node.value)) {
        throw new Error('in requires a list');
    }

    if (node.operator !== 'in' && Array.isArray(node.value)) {
        throw new Error('only in accepts a list');
    }

    if (node.operator === 'is') {
        if (Array.isArray(node.value) || !['null', 'not_null', 'true', 'false', 'unknown'].includes(node.value)) {
            throw new Error('invalid is value');
        }
    }

    return {
        kind: 'comparison',
        column: node.column,
        operator: node.operator as FilterOperator,
        negated: node.negated,
        value: node.value,
    };
}

function parseExpression(input: string): FilterNode {
    return nodeFromGenerated(generatedParser.parse(input));
}

function parseOrder(value: string): OrderNode[] | null {
    if (value === '') {
        return null;
    }

    const result: OrderNode[] = [];
    const items = value.split(',');

    for (let index = 0; index < items.length; index++) {
        const item = items[index];
        const parts = item.split('.');
        const column = parts.shift() || '';
        let direction = parts.shift() || 'asc';
        let nulls = parts.shift();
        if (['nullsfirst', 'nullslast'].includes(direction) && nulls === undefined) {
            nulls = direction;
            direction = 'asc';
        }

        if (!IDENTIFIER.test(column) || !['asc', 'desc'].includes(direction)) {
            return null;
        }

        if (parts.length > 0 || (nulls !== undefined && !['nullsfirst', 'nullslast'].includes(nulls))) {
            return null;
        }

        const order: OrderNode = {
            column: column,
            direction: direction as 'asc' | 'desc',
        };

        if (nulls !== undefined) {
            order.nulls = nulls === 'nullsfirst' ? 'first' : 'last';
        }

        result.push(order);
    }

    return result;
}

function parseInteger(value: string): number | null {
    return NONNEGATIVE_INTEGER.test(value) ? Number(value) : null;
}

function supportedOperator(value: string): boolean {
    return OPERATOR_PREFIX.test(value);
}

function parsePredicate(column: string, value: string): FilterNode | null {
    if (!IDENTIFIER.test(column)) {
        return null;
    }

    if (!supportedOperator(value)) {
        return null;
    }

    try {
        return parseExpression(`${column}.${value}`);
    } catch (error) {
        throw new Error('malformed supported predicate');
    }
}

function parseGroup(key: 'and' | 'or', value: string, negated: boolean): FilterNode | null {
    if (!value.startsWith('(')) {
        throw new Error('malformed logical group');
    }

    try {
        return parseExpression(`${negated ? 'not.' : ''}${key}${value}`);
    } catch (error) {
        return null;
    }
}

function appendFilter(filters: FilterNode[], node: FilterNode): void {
    filters.push(node);
}

function combineFilters(filters: FilterNode[]): FilterNode | undefined {
    if (filters.length === 0) {
        return undefined;
    }

    if (filters.length === 1) {
        return filters[0];
    }

    return { kind: 'group', operator: 'and', items: filters };
}

function modifierName(key: string): Modifier | null {
    return ['order', 'limit', 'offset'].includes(key) ? key as Modifier : null;
}

function parseFilter(args: string): DuckLakeFilter | undefined {
    if (args === '') {
        return undefined;
    }

    const filters: FilterNode[] = [];
    const result: DuckLakeFilter = {};
    let unsupportedPredicate = false;
    const parameters = args.split('&');

    for (let index = 0; index < parameters.length; index++) {
        const rawParameter = parameters[index];
        if (rawParameter === '') {
            continue;
        }

        const separator = rawParameter.indexOf('=');
        if (separator === -1) {
            continue;
        }

        const rawKey = rawParameter.slice(0, separator);
        const rawValue = rawParameter.slice(separator + 1);
        const key = decode(rawKey);
        const value = decode(rawValue);

        if (key === null || value === null) {
            return undefined;
        }

        const modifier = modifierName(key);
        if (modifier === 'order') {
            const order = parseOrder(value);
            if (order === null) {
                return undefined;
            }
            result.order = order;
            continue;
        }

        if (modifier === 'limit' || modifier === 'offset') {
            const number = parseInteger(value);
            if (number === null) {
                return undefined;
            }
            result[modifier] = number;
            continue;
        }

        const groupKey = key === 'and' || key === 'or'
            ? key
            : key === 'not.and'
                ? 'and'
                : key === 'not.or'
                    ? 'or'
                    : null;
        if (groupKey !== null) {
            let group: FilterNode | null;
            try {
                group = parseGroup(groupKey, value, key.startsWith('not.'));
            } catch (error) {
                return undefined;
            }
            if (group !== null) {
                appendFilter(filters, group);
            } else {
                unsupportedPredicate = true;
            }
            continue;
        }

        if (['select', 'fields'].includes(key)) {
            continue;
        }

        let predicate: FilterNode | null;
        try {
            predicate = parsePredicate(key, value);
        } catch (error) {
            return undefined;
        }

        if (predicate === null) {
            unsupportedPredicate = true;
            continue;
        }

        appendFilter(filters, predicate);
    }

    const where = combineFilters(filters);
    if (where === undefined) {
        return undefined;
    }

    result.where = where;
    if (unsupportedPredicate) {
        delete result.order;
        delete result.limit;
        delete result.offset;
    }

    return result;
}

export default { parseFilter };
