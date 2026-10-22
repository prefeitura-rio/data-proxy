import * as v from 'valibot';
import { GrantMessage, type Status } from './messages.ts';

// Runs in the notebook page. It accepts a grant only from the console that embeds this page,
// and it passes it to the Python worker. The server sends the origin of the console in config.js.
const consoleOrigin = globalThis.CONSOLE_ORIGIN;

if (consoleOrigin === undefined) {
	throw new Error('config.js did not set CONSOLE_ORIGIN');
}

const grants = new BroadcastChannel('grant');
const status = new BroadcastChannel('status');

const tell = (message: Status): void => {
	window.parent.postMessage(message, consoleOrigin);
};

window.addEventListener('message', (event: MessageEvent<unknown>) => {
	if (event.origin !== consoleOrigin || event.source !== window.parent) {
		return;
	}

	const message = v.safeParse(GrantMessage, event.data);

	if (message.success) {
		grants.postMessage(message.output);
	}
});

// The worker and the page tell the console what happens: ready, applied, error, csp, and the checks of a test notebook.
status.onmessage = (event: MessageEvent<Status>) => {
	tell(event.data);
};
document.addEventListener('securitypolicyviolation', (event) => {
	tell({ type: 'csp', directive: event.violatedDirective, blocked: event.blockedURI });
});
tell({ type: 'ready' });
