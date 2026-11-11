import { resolve } from '$app/paths';
import type { Cookies } from '@sveltejs/kit';
import { BASE } from '../base';
import { discover, refreshSession, type Discovery, type OidcConfig } from './oidc';
import { settings } from './settings';
import { chunk, isExpired, needsRefresh, seal, unchunk, unseal, type Session } from './session';

const COOKIE = 'session';
const PIECE = 3500;
const OPTIONS = { path: BASE, httpOnly: true, sameSite: 'lax', secure: false } as const;

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

	return { issuer, clientId, clientSecret, redirectUri: `${origin}${resolve('/auth/callback')}` };
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
		cookies.delete(name, { path: BASE });
	}
}

export function writeSession(cookies: Cookies, session: Session, secure: boolean): void {
	clearSession(cookies);

	chunk(seal(session, settings().cookieSecret), PIECE).forEach((piece, index) => {
		cookies.set(`${COOKIE}.${index}`, piece, { ...OPTIONS, secure });
	});
}

const refreshing = new Map<string, Promise<Session>>();

/**
 * Refresh a session once for all the requests that carry the same refresh token. The identity provider may accept
 * a refresh token only once, and the page sends parallel requests close to the end of a session.
 */
function refreshed(session: Session): Promise<Session> {
	let running = refreshing.get(session.refreshToken);

	if (running === undefined) {
		running = provider()
			.then((discovered) => refreshSession(discovered, oidcConfig(), session.refreshToken))
			.finally(() => refreshing.delete(session.refreshToken));
		refreshing.set(session.refreshToken, running);
	}

	return running;
}

/**
 * Return a session that is fresh enough to use, or null when the user must sign in again. A failed refresh keeps the
 * cookies while the token has time left, so a short failure of the identity provider does not sign the user out.
 */
export async function freshSession(cookies: Cookies, secure: boolean): Promise<Session | null> {
	const session = readSession(cookies);

	if (session === null) {
		return null;
	}

	if (!needsRefresh(session)) {
		return session;
	}

	try {
		const next = await refreshed(session);
		writeSession(cookies, next, secure);

		return next;
	} catch {
		if (!isExpired(session)) {
			return session;
		}

		clearSession(cookies);

		return null;
	}
}
