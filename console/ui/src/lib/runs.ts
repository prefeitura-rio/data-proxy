export const RUN_STATUSES = [
	'PENDING',
	'ENQUEUED',
	'DELAYED',
	'SUCCESS',
	'ERROR',
	'CANCELLED',
	'MAX_RECOVERY_ATTEMPTS_EXCEEDED'
];

const ACTIVE = ['PENDING', 'ENQUEUED', 'DELAYED'];
const RESUMABLE = ['CANCELLED', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED'];
const FINISHED = ['SUCCESS', 'ERROR', ...RESUMABLE];

export type Controls = { cancel: boolean; resume: boolean; retry: boolean };

/** Say which control buttons a run of this status gets. Running again starts a new run, so it also needs the start permission. */
export function controlsFor(status: string, permissions: string[]): Controls {
	const control = permissions.includes('control_sync');

	return {
		cancel: control && ACTIVE.includes(status),
		resume: control && RESUMABLE.includes(status),
		retry: control && permissions.includes('start_sync') && FINISHED.includes(status)
	};
}

const DAY_MS = 86_400_000;

function isDate(value: string): boolean {
	const time = Date.parse(`${value}T00:00:00Z`);

	return (
		/^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(time) && new Date(time).toISOString().slice(0, 10) === value
	);
}

const nextDay = (value: string) => new Date(Date.parse(`${value}T00:00:00Z`) + DAY_MS).toISOString().slice(0, 10);

/** Turn the filters of the address into PostgREST filters. A value that doesn't fit its field is ignored. */
export function runFilters(params: URLSearchParams): string {
	const status = params.get('status') ?? '';
	const schema = params.get('schema') ?? '';
	const from = params.get('from') ?? '';
	const to = params.get('to') ?? '';
	const id = params.get('id') ?? '';
	const parts = [
		RUN_STATUSES.includes(status) ? `status=eq.${status}` : '',
		/^[a-z][a-z0-9_]{0,55}$/.test(schema) ? `schemas=cs.${encodeURIComponent(`{${schema}}`)}` : '',
		isDate(from) ? `created_at=gte.${from}` : '',
		isDate(to) ? `created_at=lt.${nextDay(to)}` : '',
		/^[0-9a-f-]{1,36}$/.test(id) ? `id=like.${id}*` : ''
	];

	return parts.map((part) => (part === '' ? '' : `&${part}`)).join('');
}
