import type { Credentials } from './signer';

export type Policy = {
	Version: string;
	Statement: { Effect: 'Allow'; Action: string[]; Resource: string[] }[];
};

/** The calls that the storage must answer. They are the same few IAM calls that SeaweedFS and S3 both have. */
export type Iam = {
	ensureUser: (name: string) => Promise<void>;
	putPolicy: (name: string, policy: Policy) => Promise<void>;
	/** Delete the keys of the user and make one new key. */
	replaceKey: (name: string) => Promise<Credentials>;
};

export type KeyStore = {
	get: (schema: string) => Promise<{ accessKeyId: string; sealed: string } | null>;
	put: (schema: string, accessKeyId: string, sealed: string) => Promise<void>;
	/** Run the work while no other replica works on the same schema. */
	locked: <T>(schema: string, work: () => Promise<T>) => Promise<T>;
};

type Deps = {
	iam: Iam;
	store: KeyStore;
	seal: (text: string) => string;
	unseal: (text: string) => string | null;
};

export const userName = (schema: string) => `sig_${schema}`;

/** Allow reading the objects under one prefix and nothing else. */
export const readPolicy = (bucket: string, prefix: string): Policy => ({
	Version: '2012-10-17',
	Statement: [{ Effect: 'Allow', Action: ['s3:GetObject'], Resource: [`arn:aws:s3:::${bucket}/${prefix}*`] }]
});

async function stored(schema: string, deps: Deps): Promise<Credentials | null> {
	const row = await deps.store.get(schema);
	const secret = row ? deps.unseal(row.sealed) : null;

	return row && secret !== null ? { accessKeyId: row.accessKeyId, secretAccessKey: secret } : null;
}

/** Return the credential that reads the files of one schema only, and make the identity at the first call. */
export async function identityFor(
	schema: string,
	location: { bucket: string; prefix: string },
	deps: Deps
): Promise<Credentials> {
	const known = await stored(schema, deps);

	if (known) {
		return known;
	}

	return deps.store.locked(schema, async () => {
		const meanwhile = await stored(schema, deps);

		if (meanwhile) {
			return meanwhile;
		}

		const name = userName(schema);
		await deps.iam.ensureUser(name);
		await deps.iam.putPolicy(name, readPolicy(location.bucket, location.prefix));
		const credentials = await deps.iam.replaceKey(name);
		await deps.store.put(schema, credentials.accessKeyId, deps.seal(credentials.secretAccessKey));

		return credentials;
	});
}
