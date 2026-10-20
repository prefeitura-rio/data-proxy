import * as env from '$app/env/private';

export const settings = () => ({
	issuer: env.OIDC_ISSUER,
	clientId: env.OIDC_CLIENT_ID,
	clientSecret: env.OIDC_CLIENT_SECRET,
	cookieSecret: env.COOKIE_SECRET,
	postgrestUrl: env.POSTGREST_URL.replace(/\/$/, ''),
	origin: env.ORIGIN
});

export type QuerySettings = {
	notebookOrigin: string;
	catalogUrl: string;
	signer: import('./signer').SignerConfig;
	/** The admin credential of the storage and its IAM endpoint. Without it, every schema signs with the read-only credential. */
	iam: { endpoint: string; region: string; accessKeyId: string; secretAccessKey: string } | null;
	/** The secret that seals the keys of the identities in the catalog database. */
	keysSecret: string;
};

/** The settings of the query tab, or null when one is missing, so that the other tabs still work. */
export function querySettings(): QuerySettings | null {
	const { NOTEBOOK_ORIGIN, CATALOG_URL, S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY } = env;

	if (!NOTEBOOK_ORIGIN || !CATALOG_URL || !S3_ENDPOINT || !S3_BUCKET || !S3_ACCESS_KEY || !S3_SECRET_KEY) {
		return null;
	}

	return {
		notebookOrigin: NOTEBOOK_ORIGIN,
		catalogUrl: CATALOG_URL,
		signer: {
			endpoint: S3_ENDPOINT,
			region: env.S3_REGION ?? 'us-east-1',
			bucket: S3_BUCKET,
			fallback: { accessKeyId: S3_ACCESS_KEY, secretAccessKey: S3_SECRET_KEY }
		},
		iam:
			env.S3_ADMIN_ACCESS_KEY && env.S3_ADMIN_SECRET_KEY
				? {
						endpoint: env.S3_IAM_ENDPOINT ?? S3_ENDPOINT,
						region: env.S3_REGION ?? 'us-east-1',
						accessKeyId: env.S3_ADMIN_ACCESS_KEY,
						secretAccessKey: env.S3_ADMIN_SECRET_KEY // pragma: allowlist secret
					}
				: null,
		keysSecret: `${env.COOKIE_SECRET}:signing-keys`
	};
}
