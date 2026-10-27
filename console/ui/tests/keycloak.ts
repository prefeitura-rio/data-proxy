import * as v from 'valibot';

const KEYCLOAK = process.env.KEYCLOAK_URL ?? '';
const REALM = 'dev';
const CLIENT = 'data-proxy-console-web';
const LIFESPAN = 'access.token.lifespan';

const Token = v.object({ access_token: v.string() });
const Client = v.looseObject({ id: v.string(), attributes: v.optional(v.record(v.string(), v.string()), {}) });

async function adminToken(): Promise<string> {
	const response = await fetch(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
		method: 'POST',
		body: new URLSearchParams({ grant_type: 'password', client_id: 'admin-cli', username: 'admin', password: 'admin' })
	});

	return v.parse(Token, await response.json()).access_token;
}

async function readClient(token: string) {
	const response = await fetch(`${KEYCLOAK}/admin/realms/${REALM}/clients?clientId=${CLIENT}`, {
		headers: { Authorization: `Bearer ${token}` }
	});
	const [client] = v.parse(v.array(Client), await response.json());

	if (!client) {
		throw new Error(`The client ${CLIENT} does not exist`);
	}

	return client;
}

/** Set the lifespan of the access tokens of the console client, or remove the setting, and return the old value. */
export async function setTokenLifespan(seconds: string | null): Promise<string | null> {
	const token = await adminToken();
	const client = await readClient(token);
	const old = client.attributes[LIFESPAN] ?? null;
	const attributes = Object.fromEntries(Object.entries(client.attributes).filter(([key]) => key !== LIFESPAN));

	if (seconds !== null) {
		attributes[LIFESPAN] = seconds;
	}

	const response = await fetch(`${KEYCLOAK}/admin/realms/${REALM}/clients/${client.id}`, {
		method: 'PUT',
		headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
		body: JSON.stringify({ ...client, attributes })
	});

	if (!response.ok) {
		throw new Error(`Keycloak refused the client update: ${response.status}`);
	}

	return old;
}
