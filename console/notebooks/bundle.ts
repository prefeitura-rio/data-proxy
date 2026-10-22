// Bundle the page script and the worker script with their packages: DuckDB-WASM imports apache-arrow by its bare name.
import { build } from 'esbuild';

const [outdir] = process.argv.slice(2);

if (outdir === undefined) {
	throw new Error('Usage: node bundle.ts <output folder>');
}

await build({ entryPoints: ['src/helper.ts'], bundle: true, format: 'esm', outdir });
await build({ entryPoints: ['src/bridge.ts'], bundle: true, format: 'iife', outdir });
