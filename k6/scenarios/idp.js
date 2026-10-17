/** @import { Step } from '../lib/index.js' */
import {
    claimsOf,
    FULL_TABLE,
    IDP_CLIENT_ID,
    IDP_PASSWORD,
    postgrestCall,
    postgrestRequest,
    proxyGet,
    requestToken,
    rowsOf,
    scenario,
    SCHEMA,
    stable,
    waitUntil,
} from "../lib/index.js";

const { expect, expectEach } = scenario("idp");
const VIEWER = "idp_viewer";
const CONSOLE_USER = "idp_console";

/**
 * A user of the identity provider logs in with a password, as a person does through the web app.
 * @param {string} username
 * @returns {string}
 */
const tokenOf = (username) => requestToken(IDP_CLIENT_ID, { username, password: IDP_PASSWORD }).token;

/**
 * Verifies the identity provider recipe with a real client in the Keycloak realm: a *User Client Role* mapper
 * for the single `role` claim, and a multivalued *User Attribute* mapper for `schemas`. The other clients of the
 * realm set fixed claims, so this is the only check of the mappers that the recipe tells a team to configure.
 */
export function idp() {
    const viewer = tokenOf(VIEWER);
    const operator = tokenOf(CONSOLE_USER);
    const viewerClaims = claimsOf(viewer);
    const operatorClaims = claimsOf(operator);
    expect("the viewer token carries its client role as one text", viewerClaims.role === "dp_api");
    expect("the viewer token lists the schemas of its attribute", stable(viewerClaims.schemas) === stable([SCHEMA]));
    expect("the viewer token names the user", viewerClaims.preferred_username === VIEWER);
    expect("the console user token carries its client role as one text", operatorClaims.role === "dp_console");

    expect(
        "the console user token lists every value of its attribute",
        stable(operatorClaims.schemas) === stable([SCHEMA, "console_probe"]),
    );

    expectEach(
        [viewerClaims, operatorClaims],
        (claims) => `the token of ${String(claims.preferred_username)} has no other role claim`,
        (claims) => typeof claims.role === "string",
    );

    const policy = postgrestCall(SCHEMA, "POST", "/access_policy", "admin", {
        subject: VIEWER,
        unit_type: "unit",
        unit_id: "unit_1",
    });

    try {
        expect("the admin adds a policy row for the viewer", policy.status === 201);
        const path = `/${FULL_TABLE}?select=unit_id&limit=3`;
        let read = proxyGet(path, viewer);

        expect(
            "the viewer reads data through the proxy",
            waitUntil(
                30,
                () => {
                    read = proxyGet(path, viewer);

                    return read.status === 200 && rowsOf(read).length > 0;
                },
                "the policy row to reach the read replicas",
            ),
        );

        expect(
            "the viewer reads only the unit of its policy row",
            rowsOf(read).every((row) => row.unit_id === "unit_1"),
        );
    } finally {
        postgrestCall(SCHEMA, "DELETE", `/access_policy?subject=eq.${VIEWER}`, "admin");
    }

    const overview = postgrestRequest("console", "GET", "/overview", operator);
    expect("the console user reads the overview", overview.status === 200 && rowsOf(overview).length > 0);

    expect(
        "the viewer role can't read the console",
        rowsOf(postgrestRequest("console", "GET", "/overview", viewer)).length === 0,
    );
}

/** @type {Step[]} */
export const steps = [{ scenario: "idp", name: "idp", stage: 247, run: () => idp() }];
