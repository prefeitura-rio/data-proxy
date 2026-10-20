import type { Cookies } from '@sveltejs/kit';
import { discover, refreshSession, type Discovery, type OidcConfig } from './oidc';
import { settings } from './settings';
import { chunk, isExpired, needsRefresh, seal, unchunk, unseal, type Session } from './session';

const COOKIE = 'session';
const PIECE = 3500;
const OPTIONS = { path: '/', httpOnly: true, sameSite: 'lax', secure: false } as const;

let discovery: Promise<Discovery> | null = null;

export function provider(): Promise<Discovery> {
	discovery ??= discover(settings().issuer).catch((error: unknown) => {
		discovery = null;
		throw error;
	});

	return discovery;
}

export function oidcConfig(): OidcConfig {
	const { issuer, clientId, clientSecret, origin } = settings();

	return { issuer, clientId, clientSecret, redirectUri: `${origin}/auth/callback` };
}

const pieceNames = (cookies: Cookies) =>
	cookies
		.getAll()
		.map((cookie) => cookie.name)
		.filter((name) => name.startsWith(`${COOKIE}.`))
		.sort();

function readSession(cookies: Cookies): Session | null {
	const names = pieceNames(cookies);

	if (names.length === 0) {
		return null;
	}

	const sealed = unchunk(names.map((name) => cookies.get(name) ?? ''));

	return unseal(sealed, settings().cookieSecret);
}

export function clearSession(cookies: Cookies): void {
	for (const name of pieceNames(cookies)) {
		cookies.delete(name, { path: '/' });
	}
}

export function writeSession(cookies: Cookies, session: Session, secure: boolean): void {
	clearSession(cookies);

	chunk(seal(session, settings().cookieSecret), PIECE).forEach((piece, index) => {
		cookies.set(`${COOKIE}.${index}`, piece, { ...OPTIONS, secure });
	});
}

/** Return a session that is fresh enough to use, or null when the user must sign in again. */
export async function freshSession(cookies: Cookies, secure: boolean): Promise<Session | null> {
	const session = readSession(cookies);

	if (session === null) {
		return null;
	}

	if (!needsRefresh(session)) {
		return session;
	}

	try {
		const refreshed = await refreshSession(await provider(), oidcConfig(), session.refreshToken);
		writeSession(cookies, refreshed, secure);

		return refreshed;
	} catch {
		clearSession(cookies);

		return isExpired(session) ? null : session;
	}
}
