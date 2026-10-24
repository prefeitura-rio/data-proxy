import { describe, expect, it, vi } from 'vitest';
import { oidcConfig } from './auth';

vi.mock('./settings', () => ({
	settings: () => ({
		issuer: 'https://idp.example.test/realms/dev',
		clientId: 'data-proxy-console-web',
		clientSecret: 'secret',
		cookieSecret: 'cookie',
		origin: 'https://data-proxy.dados.rio'
	})
}));

describe('oidcConfig', () => {
	it('sends the identity provider back to the console path of the API host', () => {
		expect(oidcConfig().redirectUri).toBe('https://data-proxy.dados.rio/console/auth/callback');
	});
});
