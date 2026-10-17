import { check } from "k6";
import { lastRequestFields, warn } from "./log.js";

const known = new Set();

/** @type {string[]} */
const failed = [];

/**
 * Returns the name of every check that failed so far.
 * @returns {string[]}
 */
export function failedChecks() {
    return failed;
}

/**
 * Returns the names of every scenario that registered itself.
 * @returns {string[]}
 */
export function scenarioNames() {
    return [...known];
}

/**
 * Returns the check helpers of one scenario. Every check is named `scenario: description`.
 * A failed check logs a warning at once, because k6 lists failed checks only in the summary.
 * @param {string} name
 * @returns {{ checks: (conditions: any) => void; expect(description: string, ok: boolean): void; expectEach<T>(items: readonly T[], description: (item: T) => string, ok: (item: T) => boolean): void; requirePrecondition(description: string, ok: boolean, detail: unknown): void; }}
 */
export function scenario(name) {
    known.add(name);

    const named = /** @param {string} description @returns {string} */ (description) => {
        if ([...known].some((other) => description.startsWith(`${other}: `))) {
            throw new Error(`a check description must not start with a scenario name: ${description}`);
        }

        return `${name}: ${description}`;
    };

    const checks = /** @param {Record<string, () => boolean>} conditions @returns {void} */ (conditions) => {
        for (const [description, condition] of Object.entries(conditions)) {
            const checkName = named(description);
            if (check(null, { [checkName]: condition })) continue;
            failed.push(checkName);
            warn("check_failed", { scenario: name, check: description, ...lastRequestFields() });
        }
    };

    return {
        checks,
        /** Asserts a named condition and logs it as a verification step. */
        expect(description, ok) {
            checks({ [description]: () => ok });
        },
        /** Asserts one condition for each item, with one check for each item. */
        expectEach(items, description, ok) {
            for (const item of items) checks({ [description(item)]: () => ok(item) });
        },
        /** Asserts a named precondition, logs it, and throws when it fails. */
        requirePrecondition(description, ok, detail) {
            checks({ [description]: () => ok });

            if (!ok) {
                throw new Error(`precondition failed: ${named(description)}: ${JSON.stringify(detail)}`);
            }
        },
    };
}
