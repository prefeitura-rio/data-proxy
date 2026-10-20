import {
	CreateAccessKeyCommand,
	CreateUserCommand,
	DeleteAccessKeyCommand,
	EntityAlreadyExistsException,
	IAMClient,
	ListAccessKeysCommand,
	PutUserPolicyCommand
} from '@aws-sdk/client-iam';
import type { Iam } from './identities';

/** Talk to the IAM API of the storage with an admin credential. SeaweedFS answers it on the S3 port. */
export function storageIam(config: {
	endpoint: string;
	region: string;
	accessKeyId: string;
	secretAccessKey: string;
}): Iam {
	const client = new IAMClient({
		endpoint: config.endpoint,
		region: config.region,
		credentials: { accessKeyId: config.accessKeyId, secretAccessKey: config.secretAccessKey }
	});

	return {
		async ensureUser(name) {
			try {
				await client.send(new CreateUserCommand({ UserName: name }));
			} catch (error) {
				if (!(error instanceof EntityAlreadyExistsException)) {
					throw error;
				}
			}
		},
		async putPolicy(name, policy) {
			await client.send(
				new PutUserPolicyCommand({ UserName: name, PolicyName: 'read', PolicyDocument: JSON.stringify(policy) })
			);
		},
		async replaceKey(name) {
			const existing = await client.send(new ListAccessKeysCommand({ UserName: name }));

			for (const key of existing.AccessKeyMetadata ?? []) {
				await client.send(new DeleteAccessKeyCommand({ UserName: name, AccessKeyId: key.AccessKeyId }));
			}

			const created = await client.send(new CreateAccessKeyCommand({ UserName: name }));
			const key = created.AccessKey;

			if (!key?.AccessKeyId || !key.SecretAccessKey) {
				throw new Error(`The storage made no key for ${name}`);
			}

			return { accessKeyId: key.AccessKeyId, secretAccessKey: key.SecretAccessKey };
		}
	};
}
