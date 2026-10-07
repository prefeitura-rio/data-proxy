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

interface ComparisonNode {
    kind: 'comparison';
    column: string;
    operator: FilterOperator;
    negated: boolean;
    value: string | string[];
}

interface GroupNode {
    kind: 'group';
    operator: 'and' | 'or';
    negated?: boolean;
    items: FilterNode[];
}

type FilterNode = ComparisonNode | GroupNode;

interface DuckLakeFilter {
    where: FilterNode;
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

const OPERATOR_PREFIX = /^(?:not\.)?(eq|neq|gt|gte|lt|lte|in|is|like|ilike)(?:\.|$)/;
const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]*$/;

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

function parseFilter(args: string): DuckLakeFilter | undefined {
    if (args === '') {
        return undefined;
    }

    const filters: FilterNode[] = [];
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

        if (predicate !== null) {
            appendFilter(filters, predicate);
        }
    }

    const where = combineFilters(filters);
    if (where === undefined) {
        return undefined;
    }

    return { where };
}

export default { parseFilter };
