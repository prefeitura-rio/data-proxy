import { describe, expect, it } from 'vitest';
import { RUN_STATUSES } from './runs';
import { tone } from './status';

describe('tone', () => {
	it.each([
		{ status: 'SUCCESS', badge: 'badge-success' },
		{ status: 'ACTIVE', badge: 'badge-success' },
		{ status: 'ERROR', badge: 'badge-error' },
		{ status: 'MAX_RECOVERY_ATTEMPTS_EXCEEDED', badge: 'badge-error' },
		{ status: 'PENDING', badge: 'badge-info' },
		{ status: 'ENQUEUED', badge: 'badge-info' },
		{ status: 'DELAYED', badge: 'badge-info' },
		{ status: 'PAUSED', badge: 'badge-warning' },
		{ status: 'CANCELLED', badge: 'badge-neutral' },
		{ status: 'SOMETHING_NEW', badge: 'badge-neutral' }
	])('gives $status the badge $badge', ({ status, badge }) => {
		expect(tone(status)).toBe(badge);
	});

	it('gives every run status a badge', () => {
		expect(RUN_STATUSES.every((status) => tone(status).startsWith('badge-'))).toBe(true);
	});
});
