/** @import { Response as K6Response } from 'k6/http' */
const BODY_LIMIT = 200;
const HEARTBEAT_SECONDS = 30;

/** @type {Fields} */
let current = { scenario: "setup" };

/** @type {Fields | undefined} */
let lastRequest;

/**
 * @param {string | number | boolean} value
 * @returns {string}
 */
const quote = (value) => {
    const text = String(value);

    return /^[\w.:/@+-]+$/.test(text) ? text : JSON.stringify(text);
};

/**
 * Names the scenario of the log lines outside a step. Each suite sets it once, when it loads.
 * @param {string} name
 */
export function setScenario(name) {
    current = { scenario: name };
}

/**
 * Renders `scenario=x step=y event=z key=value`. The line names the running step, and a field can replace it.
 * @param {string} event
 * @param {Fields} fields
 * @returns {string}
 */
function render(event, fields) {
    /** @type {Fields} */
    const all = { ...current, event, ...fields };

    return Object.entries(all)
        .filter(/** @returns {entry is [string, string | number | boolean]} */ (entry) => entry[1] !== undefined)
        .map(([key, value]) => `${key}=${quote(value)}`)
        .join(" ");
}

/**
 * Writes one progress line to the log of the runner.
 * @param {string} event
 * @param {Fields} [fields={}]
 */
export function info(event, fields = {}) {
    console.log(render(event, fields));
}

/**
 * Writes one warning line to the log of the runner.
 * @param {string} event
 * @param {Fields} [fields={}]
 */
export function warn(event, fields = {}) {
    console.warn(render(event, fields));
}

/**
 * Keeps the last response of the suite, so that a failed check can say which request it came after.
 * @param {K6Response} response
 * @returns {K6Response}
 */
export function rememberRequest(response) {
    lastRequest = {
        last_request: `${response.request.method} ${response.request.url.replace(/^https?:\/\/[^/]+/, "")}`,
        last_status: response.status,
        last_body: String(response.body ?? "").slice(0, BODY_LIMIT),
    };

    return response;
}

/**
 * The fields of the last request, for a failed check.
 * @returns {Fields}
 */
export function lastRequestFields() {
    return lastRequest ?? {};
}

/**
 * Runs one step: its log lines name it, and the log says when it started, how long it took, and whether it threw.
 * @template T
 * @param {string} scenario
 * @param {string} step
 * @param {() => T} run
 * @returns {T}
 */
export function inStep(scenario, step, run) {
    const previous = current;
    const started = Date.now();
    current = { scenario, step };
    lastRequest = undefined;
    info("step_started");

    try {
        return run();
    } catch (error) {
        warn("step_failed", { error: String(error).slice(0, BODY_LIMIT * 2) });
        throw error;
    } finally {
        info("step_done", { seconds: Math.round((Date.now() - started) / 1000) });
        current = previous;
    }
}

/**
 * Returns a function that logs `event` at most once every 30 seconds, with the time since it was made.
 * @param {string} event
 * @param {Fields} fields
 * @returns {() => void}
 */
export function heartbeat(event, fields) {
    const started = Date.now();
    let next = started + HEARTBEAT_SECONDS * 1000;

    return () => {
        const now = Date.now();
        if (now < next) return;
        next = now + HEARTBEAT_SECONDS * 1000;
        info(event, { ...fields, elapsed: Math.round((now - started) / 1000) });
    };
}
