import { beforeEach, describe, expect, it, vi } from 'vitest';
import { EntityAlreadyExistsException } from '@aws-sdk/client-iam';
import { storageIam } from './iam';
import { readPolicy } from './identities';

type Sent = { kind: string; input: Record<string, unknown> };

const send = vi.hoisted(() => vi.fn<(command: Sent) => Promise<unknown>>());

vi.mock('@aws-sdk/client-iam', () => {
	const command = (kind: string) =>
		class {
			readonly kind = kind;
			constructor(readonly input: Record<string, unknown>) {}
		};

	return {
		IAMClient: class {
			send = send;
		},
		EntityAlreadyExistsException: class extends Error {},
		CreateUserCommand: command('CreateUser'),
		PutUserPolicyCommand: command('PutUserPolicy'),
		ListAccessKeysCommand: command('ListAccessKeys'),
		DeleteAccessKeyCommand: command('DeleteAccessKey'),
		CreateAccessKeyCommand: command('CreateAccessKey')
	};
});

const iam = storageIam({
	endpoint: 'http://storage:8333',
	region: 'us-east-1',
	accessKeyId: 'admin',
	secretAccessKey: 'admin-secret'
});

const kinds = () => send.mock.calls.map(([sent]) => sent.kind);

beforeEach(() => {
	send.mockReset();
});

describe('storageIam.ensureUser', () => {
	it('creates the user', async () => {
		send.mockResolvedValue({});

		await iam.ensureUser('sales');

		expect(send.mock.calls.map(([sent]) => [sent.kind, sent.input])).toEqual([['CreateUser', { UserName: 'sales' }]]);
	});

	it('accepts a user that exists', async () => {
		send.mockRejectedValue(new EntityAlreadyExistsException({ message: 'exists', $metadata: {} }));

		await expect(iam.ensureUser('sales')).resolves.toBeUndefined();
	});

	it('raises every other error', async () => {
		send.mockRejectedValue(new Error('storage is down'));

		await expect(iam.ensureUser('sales')).rejects.toThrow('storage is down');
	});
});

describe('storageIam.putPolicy', () => {
	it('writes the policy as JSON under the name read', async () => {
		send.mockResolvedValue({});

		const policy = readPolicy('data', 'ducklake/sales/');

		await iam.putPolicy('sales', policy);

		expect(send.mock.calls.map(([sent]) => [sent.kind, sent.input])).toEqual([
			['PutUserPolicy', { UserName: 'sales', PolicyName: 'read', PolicyDocument: JSON.stringify(policy) }]
		]);
	});
});

describe('storageIam.replaceKey', () => {
	const answers: Record<string, unknown> = {
		ListAccessKeys: { AccessKeyMetadata: [{ AccessKeyId: 'a' }, { AccessKeyId: 'b' }] },
		DeleteAccessKey: {},
		CreateAccessKey: { AccessKey: { AccessKeyId: 'new', SecretAccessKey: 'new-secret' } }
	};

	it('deletes every old key before it creates one', async () => {
		send.mockImplementation((sent) => Promise.resolve(answers[sent.kind]));

		const key = await iam.replaceKey('sales');

		expect(kinds()).toEqual(['ListAccessKeys', 'DeleteAccessKey', 'DeleteAccessKey', 'CreateAccessKey']);
		expect(send.mock.calls.filter(([sent]) => sent.kind === 'DeleteAccessKey').map(([sent]) => sent.input)).toEqual([
			{ UserName: 'sales', AccessKeyId: 'a' },
			{ UserName: 'sales', AccessKeyId: 'b' }
		]);
		expect(key).toEqual({ accessKeyId: 'new', secretAccessKey: 'new-secret' });
	});

	it('creates a key for a user that has none', async () => {
		send.mockImplementation((sent) =>
			Promise.resolve(sent.kind === 'ListAccessKeys' ? { AccessKeyMetadata: undefined } : answers[sent.kind])
		);

		await iam.replaceKey('sales');

		expect(kinds()).toEqual(['ListAccessKeys', 'CreateAccessKey']);
	});

	it('raises when the storage gives no key', async () => {
		send.mockImplementation((sent) =>
			Promise.resolve(sent.kind === 'CreateAccessKey' ? { AccessKey: { AccessKeyId: 'new' } } : answers[sent.kind])
		);

		await expect(iam.replaceKey('sales')).rejects.toThrow('The storage made no key for sales');
	});
});
