import type { Status } from '../src/messages.ts';

declare global {
	interface Window {
		statuses: Status[];
		seen: string[];
	}
}
