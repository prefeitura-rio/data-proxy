export type Sources = Record<string, string[]>;

/** Add sources to the directives of a policy, and add each directive that the policy lacks. */
export function withSources(policy: string, extra: Sources): string {
	const directives = policy.split(';').map((directive) => directive.trim());
	const known = new Set(directives.map((directive) => directive.split(' ')[0]));
	const merged = directives.map((directive) => {
		const name = directive.split(' ')[0] ?? '';
		const added = extra[name];

		return added === undefined ? directive : [directive, ...added].join(' ');
	});
	const created = Object.entries(extra)
		.filter(([name]) => !known.has(name))
		.map(([name, sources]) => [name, ...sources].join(' '));

	return [...merged, ...created].join('; ');
}

/** Set the security headers of a response, and add the sources that only the settings know to its policy. */
export function securityHeaders(headers: Headers, extra: Sources): void {
	const policy = headers.get('content-security-policy');

	if (policy !== null) {
		headers.set('content-security-policy', withSources(policy, extra));
	}

	headers.set('x-content-type-options', 'nosniff');
	headers.set('referrer-policy', 'same-origin');

	if (!headers.has('cache-control')) {
		headers.set('cache-control', 'no-store');
	}
}

/** The sources that depend on the settings: the notebook frame and the sign-out address of the identity provider. */
export function settingsSources(notebookOrigin: string | null, issuer: string): Sources {
	return {
		'form-action': [new URL(issuer).origin],
		...(notebookOrigin === null ? {} : { 'frame-src': [notebookOrigin] })
	};
}
