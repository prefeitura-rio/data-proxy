import http from "k6/http";
import { sleep } from "k6";
import encoding from "k6/encoding";
import { OIDC_CLIENT_SECRET, OIDC_TOKEN_URL, OIDC_USER_CLIENT_ID, TOKEN_REFRESH_SECONDS } from "./env.js";
import { isRecord, safeJson } from "./http.js";

/**
 * Fetches an OIDC access token, retrying up to five times. A user gets a password grant, and a service the client credentials.
 * @param {string} clientId
 * @param {{ username: string; password: string }} [user]
 * @returns {{ token: string; lifetime: number }}
 */
export function requestToken(clientId, user) {
    const grant =
        user === undefined
            ? { grant_type: "client_credentials" }
            : { grant_type: "password", username: user.username, password: user.password };

    for (let attempt = 0; attempt < 5; attempt++) {
        const response = http.post(OIDC_TOKEN_URL, {
            ...grant,
            client_id: clientId,
            client_secret: OIDC_CLIENT_SECRET,
        });

        const body = safeJson(response);

        if (isRecord(body) && typeof body.access_token === "string") {
            return { token: body.access_token, lifetime: typeof body.expires_in === "number" ? body.expires_in : 300 };
        }

        sleep(2);
    }

    throw new Error(`Token request failed for client: ${clientId}`);
}

/**
 * @param {string} [clientId=OIDC_USER_CLIENT_ID]
 * @returns {string}
 */
export function fetchToken(clientId = OIDC_USER_CLIENT_ID) {
    return requestToken(clientId).token;
}

/** @type {Record<string, { token: string; expiresAt: number }>} */
const sessions = {};

/**
 * Returns a token for one client, refreshed before it expires.
 * @param {string} clientId
 * @returns {string}
 */
export function clientToken(clientId) {
    const session = sessions[clientId];

    if (session !== undefined && Date.now() < session.expiresAt) {
        return session.token;
    }

    const { token, lifetime } = requestToken(clientId);

    sessions[clientId] = {
        token,
        expiresAt: Date.now() + (lifetime - TOKEN_REFRESH_SECONDS) * 1000,
    };

    return token;
}

/**
 * Reads the claims of a token. The signature is not checked: PostgREST checks it.
 * @param {string} token
 * @returns {Record<string, unknown>}
 */
export function claimsOf(token) {
    const claims = JSON.parse(encoding.b64decode(token.split(".")[1], "rawurl", "s"));

    return isRecord(claims) ? claims : {};
}

/**
 * Reads the claims of the token of one client.
 * @param {string} clientId
 * @returns {Record<string, unknown>}
 */
export function tokenClaims(clientId) {
    return claimsOf(clientToken(clientId));
}

/**
 * Returns a token for the test user, refreshed before it expires.
 * @returns {string}
 */
export function userToken() {
    return clientToken(OIDC_USER_CLIENT_ID);
}
