export type ToastKind = 'success' | 'error';

/** A toast may link to a sync run, because starting a run is the one action whose result lives on another page. */
export type Toast = { id: number; kind: ToastKind; text: string; run?: string };

/** How long a toast stays, in milliseconds. */
export const TOAST_MS = 4000;

/** The toasts on screen, oldest first. Components read it, and only the functions below change it. */
export const toasts = $state<Toast[]>([]);

let counter = 0;

export function dismiss(id: number): void {
	const index = toasts.findIndex((toast) => toast.id === id);

	if (index >= 0) {
		toasts.splice(index, 1);
	}
}

/** Show a toast. The same text twice in a row shows once, so a double click doesn't pile up. */
export function notify(toast: Omit<Toast, 'id'>, lifetime = TOAST_MS): void {
	if (toasts.some((shown) => shown.text === toast.text && shown.kind === toast.kind)) {
		return;
	}

	counter += 1;
	const id = counter;

	toasts.push({ ...toast, id });
	setTimeout(() => {
		dismiss(id);
	}, lifetime);
}

export const success = (text: string, run?: string): void => {
	notify({ kind: 'success', text, run });
};
