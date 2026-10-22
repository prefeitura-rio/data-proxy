import { sleep } from "k6";
import { POLL_INTERVAL } from "./env.js";
import { heartbeat, info, warn } from "./log.js";

/**
 * Polls until the probe holds or the timeout passes. A label makes it log what it waits for and how long it took.
 * @param {number} timeoutSeconds
 * @param {() => boolean} probe
 * @param {string} [label]
 * @returns {boolean}
 */
export function waitUntil(timeoutSeconds, probe, label) {
    const started = Date.now();
    const seconds = () => Math.round((Date.now() - started) / 1000);
    const beat = heartbeat("waiting", { label, timeout: timeoutSeconds });
    if (label !== undefined) info("wait_started", { label, timeout: timeoutSeconds });
    const deadline = started + timeoutSeconds * 1000;

    while (Date.now() < deadline) {
        if (probe()) {
            if (label !== undefined) info("wait_done", { label, seconds: seconds() });

            return true;
        }

        if (label !== undefined) beat();
        sleep(POLL_INTERVAL);
    }

    const ok = probe();
    if (label !== undefined) (ok ? info : warn)(ok ? "wait_done" : "wait_timed_out", { label, seconds: seconds() });

    return ok;
}
