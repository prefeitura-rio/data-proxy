import { GetObjectCommand, S3Client } from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';

export type Credentials = { accessKeyId: string; secretAccessKey: string };

export type SignerConfig = {
	endpoint: string;
	region: string;
	bucket: string;
	/** How long a signed link stays valid, in seconds. */
	ttlSeconds: number;
	/** The read-only credential that signs when no identity exists for the schema. */
	fallback: Credentials;
};

/** Return a function that signs a GET link for one key, valid for the lifetime of the settings, with the given credential. */
export function signWith(config: SignerConfig, credentials: Credentials) {
	const client = new S3Client({
		endpoint: config.endpoint,
		region: config.region,
		forcePathStyle: true,
		credentials
	});

	return (key: string) =>
		getSignedUrl(client, new GetObjectCommand({ Bucket: config.bucket, Key: key }), { expiresIn: config.ttlSeconds });
}
