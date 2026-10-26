import * as v from 'valibot';
import { untrack } from 'svelte';
import { resolve } from '$app/paths';
import { currentTheme } from './theme.js';
import { Grant } from './types.js';

const Ready = v.object({ type: v.literal('ready') });
const Refusal = v.object({ message: v.string() });

/** The notebook takes this long, in milliseconds, before the page says that it didn't answer. */
const READY_TIMEOUT_MS = 15_000;

/** The share of the life of a grant after which the page asks for a new one. */
const RENEW_SHARE = 0.8;

export type Target = { schema: string; tables: string[] };

export type BridgeSource = {
	origin: () => string | null;
	ttlSeconds: () => number;
	frame: () => HTMLIFrameElement | undefined;
	target: () => Target | null;
	/** A number that changes when the reader reloads the frame. */
	attempt: () => number;
};

/**
 * Talk to the notebook in its frame: send the theme when the notebook is ready, send a grant for the open tables and
 * renew it before it ends. It tells whether the notebook answered. Call it while a component starts.
 */
export function notebookBridge(source: BridgeSource) {
	let ready = $state(false);
	let timedOut = $state(false);
	let message = $state('');

	const post = (payload: unknown) => source.frame()?.contentWindow?.postMessage(payload, source.origin() ?? '');

	async function send(target: Target) {
		const response = await fetch(resolve('/query/grant'), {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(target)
		});

		if (!response.ok) {
			const body = v.safeParse(Refusal, await response.json().catch(() => null));

			message =
				response.status === 409
					? 'The files of this schema are encrypted, so the notebook can not read them.'
					: body.success
						? body.output.message
						: 'The grant failed.';
			return;
		}

		message = '';
		post({ type: 'grant', grant: v.parse(Grant, await response.json()) });
	}

	const sendTheme = () => post({ type: 'theme', theme: currentTheme() });

	$effect(() => {
		const target = source.target();

		void source.attempt();

		if (!target || !source.origin()) {
			return;
		}

		ready = false;
		timedOut = false;

		const listener = (event: MessageEvent) => {
			if (
				event.origin === source.origin() &&
				event.source === source.frame()?.contentWindow &&
				v.is(Ready, event.data)
			) {
				ready = true;
				sendTheme();
				void send(target);
			}
		};
		const themes = new MutationObserver(sendTheme);

		themes.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
		window.addEventListener('message', listener);

		const renewal = setInterval(() => void send(target), source.ttlSeconds() * 1000 * RENEW_SHARE);
		const waiting = setTimeout(() => {
			timedOut = !untrack(() => ready);
		}, READY_TIMEOUT_MS);

		return () => {
			clearTimeout(waiting);
			window.removeEventListener('message', listener);
			themes.disconnect();
			clearInterval(renewal);
		};
	});

	return {
		get ready() {
			return ready;
		},
		get timedOut() {
			return timedOut;
		},
		get message() {
			return message;
		}
	};
}
