/** The DaisyUI badge color of a status of a run, a task or a schedule. An unknown status is neutral. */
export function tone(status: string): string {
	switch (status) {
		case 'SUCCESS':
		case 'ACTIVE':
			return 'badge-success';
		case 'ERROR':
		case 'MAX_RECOVERY_ATTEMPTS_EXCEEDED':
			return 'badge-error';
		case 'PENDING':
		case 'ENQUEUED':
		case 'DELAYED':
			return 'badge-info';
		case 'PAUSED':
			return 'badge-warning';
		default:
			return 'badge-neutral';
	}
}
