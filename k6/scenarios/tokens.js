import { fetchToken, proxyGet, scenario, FULL_TABLE, userToken } from "../lib/index.js";
import { sleep } from "k6";

const { expect } = scenario("tokens");

/**
 * The access token of the `expired` client lives five seconds. The gateway still accepts a token for 60 seconds after its expiry (the clock skew of Envoy).
 * @type {number}
 */
const EXPIRED_AFTER_SECONDS = 5 + 60;
const REFUSED = [401, 403];

/**
 * Verifies that the gateway refuses an expired token and a token for another audience.
 */
export function tokens() {
    const path = `/${FULL_TABLE}?select=id&limit=1`;
    expect("a valid token is served", proxyGet(path, userToken()).status === 200);
    const expired = fetchToken("expired");
    expect("a token is served in its first seconds", proxyGet(path, expired).status === 200);
    sleep(EXPIRED_AFTER_SECONDS + 5);
    expect("the gateway refuses an expired token", REFUSED.includes(proxyGet(path, expired).status));
    const other = fetchToken("wrong_audience");
    expect("the gateway refuses a token for another audience", REFUSED.includes(proxyGet(path, other).status));
    expect("a valid token is still served afterwards", proxyGet(path, userToken()).status === 200);
}

/** @type {Step[]} */
export const steps = [{ scenario: "tokens", name: "tokens", stage: 245, run: () => tokens() }];
