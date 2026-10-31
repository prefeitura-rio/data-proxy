import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const run = promisify(execFile);

/** Run SQL on the application database as its owner, for the steps that set up or clean up data. */
export async function sql(statement: string): Promise<void> {
	const url = process.env.INTEGRATION_DATABASE_URL;

	if (!url) {
		throw new Error('INTEGRATION_DATABASE_URL names the application database');
	}

	await run('psql', [url, '--no-psqlrc', '--set=ON_ERROR_STOP=1', '--command', statement]);
}
