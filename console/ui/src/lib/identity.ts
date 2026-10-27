import * as v from 'valibot';

const Claims = v.object({
	preferred_username: v.optional(v.string()),
	sub: v.optional(v.string()),
	role: v.optional(v.string())
});

export type Identity = { name: string; role: string };

/** Read the user name and the role from the payload of an access token. PostgREST verifies the token; this only labels the page. */
export function identityOf(accessToken: string): Identity | null {
	const payload = accessToken.split('.')[1];
	const claims = v.safeParse(
		v.pipe(
			v.string(),
			v.transform((text) => Buffer.from(text, 'base64url').toString('utf8')),
			v.parseJson(),
			Claims
		),
		payload
	);

	if (!claims.success) {
		return null;
	}

	const name = claims.output.preferred_username ?? claims.output.sub;

	return name === undefined ? null : { name, role: claims.output.role ?? '' };
}
