import { defineEnvVars } from '@sveltejs/kit/env';

const text = (name: string) => (value: string | undefined) => {
	if (!value) {
		throw new Error(`The setting ${name} is missing`);
	}

	return value;
};

const optional = (value: string | undefined) => (value === undefined || value === '' ? undefined : value);

export const variables = defineEnvVars({
	OIDC_ISSUER: { schema: text('OIDC_ISSUER'), description: 'The issuer URL of the identity provider' },
	OIDC_CLIENT_ID: { schema: text('OIDC_CLIENT_ID'), description: 'The id of the confidential client' },
	OIDC_CLIENT_SECRET: { schema: text('OIDC_CLIENT_SECRET'), description: 'The secret of the client' },
	COOKIE_SECRET: { schema: text('COOKIE_SECRET'), description: 'The secret that seals the session cookie' },
	POSTGREST_URL: { schema: text('POSTGREST_URL'), description: 'The address of PostgREST' },
	ORIGIN: { schema: text('ORIGIN'), description: 'The public origin of the console' },
	NOTEBOOK_ORIGIN: {
		schema: optional,
		description: 'The origin of the notebook site. Without it the query tab is off'
	},
	CATALOG_URL: { schema: optional, description: 'The catalog database URL for the console_signer login' },
	S3_ENDPOINT: { schema: optional, description: 'The storage endpoint that the signed links use' },
	S3_REGION: { schema: optional, description: 'The storage region, us-east-1 by default' },
	S3_BUCKET: { schema: optional, description: 'The bucket that holds the data files' },
	S3_ACCESS_KEY: { schema: optional, description: 'The read-only access key' },
	S3_SECRET_KEY: { schema: optional, description: 'The read-only secret key' },
	S3_ADMIN_ACCESS_KEY: {
		schema: optional,
		description: 'The admin access key of the storage. With it, each schema gets its own identity'
	},
	S3_ADMIN_SECRET_KEY: { schema: optional, description: 'The admin secret key of the storage' },
	S3_IAM_ENDPOINT: { schema: optional, description: 'The IAM endpoint of the storage, the S3 endpoint by default' }
});
