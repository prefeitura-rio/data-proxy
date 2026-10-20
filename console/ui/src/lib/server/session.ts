import { createCipheriv, createDecipheriv, createHash, randomBytes } from 'node:crypto';
import * as v from 'valibot';

const Session = v.object({ accessToken: v.string(), refreshToken: v.string(), expiresAt: v.number() });

export type Session = v.InferOutput<typeof Session>;

export const REFRESH_BEFORE_MS = 60_000;

const key = (secret: string) => createHash('sha256').update(secret).digest();

export function sealText(text: string, secret: string): string {
	const iv = randomBytes(12);
	const cipher = createCipheriv('aes-256-gcm', key(secret), iv);
	const body = Buffer.concat([cipher.update(text, 'utf8'), cipher.final()]);

	return Buffer.concat([iv, cipher.getAuthTag(), body]).toString('base64url');
}

export function unsealText(value: string, secret: string): string | null {
	try {
		const raw = Buffer.from(value, 'base64url');
		const decipher = createDecipheriv('aes-256-gcm', key(secret), raw.subarray(0, 12));
		decipher.setAuthTag(raw.subarray(12, 28));

		return Buffer.concat([decipher.update(raw.subarray(28)), decipher.final()]).toString('utf8');
	} catch {
		return null;
	}
}

export const seal = (session: Session, secret: string) => sealText(JSON.stringify(session), secret);

export function unseal(value: string, secret: string): Session | null {
	const text = unsealText(value, secret);

	if (text === null) {
		return null;
	}

	const session = v.safeParse(v.pipe(v.string(), v.parseJson(), Session), text);

	return session.success ? session.output : null;
}

export const isExpired = (session: Session, now = Date.now()) => session.expiresAt <= now;

export const needsRefresh = (session: Session, now = Date.now()) => session.expiresAt - now <= REFRESH_BEFORE_MS;

export function chunk(value: string, size: number): string[] {
	const pieces: string[] = [];

	for (let start = 0; start < value.length; start += size) {
		pieces.push(value.slice(start, start + size));
	}

	return pieces.length > 0 ? pieces : [''];
}

export const unchunk = (pieces: string[]) => pieces.join('');
