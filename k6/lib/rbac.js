/** @import { Response as K6Response } from 'k6/http' */
import { SCHEMA } from "./env.js";
import { consoleCall, postgrestCall } from "./console.js";

/**
 * Runs a probe against a row that the admin creates before it and removes after it.
 * @param {() => void} create
 * @param {() => void} remove
 * @param {() => boolean} probe
 * @returns {boolean}
 */
export function withRow(create, remove, probe) {
    remove();
    create();

    try {
        return probe();
    } finally {
        remove();
    }
}

export const nothing = () => undefined;

/**
 * @returns {K6Response}
 */
export const createSchema = () =>
    consoleCall("POST", "/schemas", "admin", {
        project: "default",
        short_name: PROBE_SCHEMA.replace("default__", ""),
        source: "bigquery",
        settings: { project: "probe" },
    });

/**
 * @returns {K6Response}
 */
export const removeSchema = () => consoleCall("DELETE", `/schemas?name=eq.${PROBE_SCHEMA}`, "admin");
export const removeGrantProbeSchema = () =>
    consoleCall("DELETE", `/schemas?name=eq.${GRANT_PROBE_SCHEMA}`, "admin");

/**
 * @returns {K6Response}
 */
export const createPolicy = () => postgrestCall("access", "POST", "/policy", "admin", policyRow);

/**
 * @returns {K6Response}
 */
export const removePolicy = () =>
    postgrestCall("access", "DELETE", `/policy?subject=eq.${PROBE_SUBJECT}`, "admin");

export const PROBE_SCHEMA = "default__e2e_probe";
export const GRANT_PROBE_SCHEMA = "default__grant_probe";
export const PROBE_SUBJECT = "e2e_rbac_probe";
export const policyRow = { scope: SCHEMA.replace("__", "."), subject: PROBE_SUBJECT, unit_type: "unit", unit_id: "unit_probe" };

/**
 * The permissions that a role holds in a rules file: its own and those of the roles that it inherits, at any depth.
 * @param {RbacRules} rules
 * @param {string} name
 * @param {Set<string>} [seen=new Set()]
 * @returns {Set<string>}
 */
export function rbacClosure(rules, name, seen = new Set()) {
    const role = rules.roles.find((candidate) => candidate.name === name);
    if (role === undefined || seen.has(name)) return new Set();
    seen.add(name);
    const held = new Set(role.permissions ?? []);

    (role.inherits ?? []).forEach((parent) =>
        rbacClosure(rules, parent, seen).forEach((permission) => held.add(permission)),
    );

    return held;
}
