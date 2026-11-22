// Bundle the page script and the worker script with their packages: DuckDB-WASM imports apache-arrow by its bare name.
import { build } from 'rolldown';

const [outdir] = process.argv.slice(2);

if (outdir === undefined) {
	throw new Error('Usage: node bundle.ts <output folder>');
}

await build({ input: 'src/helper.ts', output: { dir: outdir, format: 'esm' }, platform: 'browser' });
await build({ input: 'src/bridge.ts', output: { dir: outdir, format: 'iife' }, platform: 'browser' });
