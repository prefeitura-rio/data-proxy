export {};

declare global {
	var CONSOLE_ORIGIN: string | undefined;
	var dataProxy: { first: Promise<void>; query: (sql: string, limit: number) => Promise<string> } | undefined;
}
