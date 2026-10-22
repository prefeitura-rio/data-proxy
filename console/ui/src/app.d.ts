import type { Session } from '#lib/server/session.js';

declare global {
	namespace App {
		interface Locals {
			session: Session;
		}
	}
}

export {};
