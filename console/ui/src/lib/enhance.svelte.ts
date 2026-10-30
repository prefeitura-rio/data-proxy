import * as v from 'valibot';
import type { SubmitFunction } from '$app/forms';
import { success } from './toast.svelte.js';

export type SubmissionOptions = {
	/** Run when the server accepts the action, before the page reloads its data. */
	onsuccess?: () => void;
	/** Run when the server accepts the action and the page has its fresh data. */
	onupdated?: () => void;
	/** Keep a refusal in `error`, for a dialog that stays open, and leave the `form` of the page alone. */
	inline?: boolean;
	/** Change the page before the answer comes. Return the function that undoes the change. */
	begin?: () => () => void;
};

export type Settled = Pick<Parameters<Exclude<Awaited<ReturnType<SubmitFunction>>, void>>[0], 'result' | 'update'>;

/** What a form action returns when it worked: the text of a toast, and the id of a run to link to. */
const Done = v.object({ toast: v.string(), run: v.optional(v.string()) });

const Refusal = v.object({ message: v.string() });

/**
 * The state and the behavior of one enhanced form. `pending` disables the submit button while the request runs, so a
 * double click sends one request. A success shows its toast, and the form resets as `use:enhance` does by default.
 */
export class Submission {
	pending = $state(false);
	error = $state<string | null>(null);
	readonly #options: SubmissionOptions;

	constructor(options: SubmissionOptions = {}) {
		this.#options = options;
	}

	submit = () => {
		this.pending = true;
		this.error = null;
		const undo = this.#options.begin?.();

		return async ({ result, update }: Settled) => {
			try {
				if (result.type === 'success' || result.type === 'redirect') {
					const done = result.type === 'success' ? v.safeParse(Done, result.data) : null;

					if (done?.success) {
						success(done.output.toast, done.output.run);
					}

					this.#options.onsuccess?.();
					await update();
					undo?.();
					this.#options.onupdated?.();
				} else {
					undo?.();

					if (result.type === 'failure' && this.#options.inline) {
						const refusal = v.safeParse(Refusal, result.data);

						this.error = refusal.success ? refusal.output.message : 'The request failed.';
					} else {
						await update();
					}
				}
			} finally {
				this.pending = false;
			}
		};
	};
}
