import * as v from 'valibot';
import { error } from '@sveltejs/kit';
import { api, need } from '#lib/server/api.js';
import { follow, frame, type Source } from '#lib/server/logstream.js';
import { LogLine, Run } from '#lib/types.js';
import type { RequestHandler } from './$types';

const READ_MS = 1000;
const PING_MS = 15_000;
const SETTLE_MS = 30_000;

const sleep = (signal: AbortSignal) =>
	new Promise<void>((resolve) => {
		const timer = setTimeout(resolve, READ_MS);

		signal.addEventListener(
			'abort',
			() => {
				clearTimeout(timer);
				resolve();
			},
			{ once: true }
		);
	});

/** The token ran out or PostgREST failed. The stream ends, and the browser reconnects with a fresh token. */
const endStream = (): void => undefined;

/** Stream the status and the new log lines of a run as server-sent events, until the run finishes or the client leaves. */
export const GET: RequestHandler = async ({ locals, params, url, request }) => {
	const client = api(locals.session);
	const id = encodeURIComponent(params.id);
	const known = need(await client.get(`/runs?id=eq.${id}`, v.array(Run)))[0];

	if (!known) {
		error(404, 'No such run');
	}

	const source: Source = {
		status: async () => need(await client.get(`/runs?id=eq.${id}`, v.array(Run)))[0]?.status ?? known.status,
		lines: async (after) =>
			need(
				await client.get(
					`/logs?run_id=eq.${id}${after ? `&logged_at=gte.${encodeURIComponent(after)}` : ''}&order=logged_at.asc&limit=1000`,
					v.array(LogLine)
				)
			)
	};
	const after = request.headers.get('last-event-id') ?? url.searchParams.get('after');
	const stop = new AbortController();
	const encoder = new TextEncoder();
	let ping: ReturnType<typeof setInterval> | undefined;

	request.signal.addEventListener(
		'abort',
		() => {
			stop.abort();
		},
		{ once: true }
	);

	const body = new ReadableStream<Uint8Array>({
		async start(controller) {
			ping = setInterval(() => {
				controller.enqueue(encoder.encode(': ping\n\n'));
			}, PING_MS);

			try {
				for await (const event of follow(source, {
					after,
					signal: stop.signal,
					wait: sleep,
					now: Date.now,
					settleMs: url.searchParams.has('settle') ? SETTLE_MS : 0
				})) {
					controller.enqueue(encoder.encode(frame(event)));
				}
			} catch {
				endStream();
			} finally {
				clearInterval(ping);
				controller.close();
			}
		},
		cancel() {
			clearInterval(ping);
			stop.abort();
		}
	});

	return new Response(body, {
		headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' }
	});
};
