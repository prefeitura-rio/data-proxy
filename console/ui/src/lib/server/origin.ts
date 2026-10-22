const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

export const sameOrigin = (method: string, origin: string | null, expected: string) =>
	SAFE_METHODS.has(method) || origin === expected;
