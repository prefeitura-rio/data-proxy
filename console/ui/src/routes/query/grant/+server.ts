import { error } from '@sveltejs/kit';
import * as v from 'valibot';
import { api } from '#lib/server/api.js';
import { listFiles } from '#lib/server/catalog.js';
import { createGrant, prefixOf } from '#lib/server/grant.js';
import { identityFor } from '#lib/server/identities.js';
import { storageIam } from '#lib/server/iam.js';
import { catalogKeys } from '#lib/server/keys.js';
import { sealText, unsealText } from '#lib/server/session.js';
import { querySettings } from '#lib/server/settings.js';
import { signWith } from '#lib/server/signer.js';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ locals, request }) => {
	const settings = querySettings();

	if (settings === null) {
		error(503, 'The query tab is not set up');
	}

	const body = v.safeParse(
		v.object({ schema: v.string(), tables: v.array(v.string()) }),
		await request.json().catch(() => null)
	);
	const { schema, tables } = body.success ? body.output : { schema: '', tables: [] };
	const client = api(locals.session);

	const result = await createGrant(schema, tables, {
		authorize: (name, table) =>
			client.get(
				`/rpc/table_files?schema_name=${encodeURIComponent(name)}&table_name=${encodeURIComponent(table)}`,
				v.unknown()
			),
		list: (name, names) => listFiles(settings.catalogUrl, name, names),
		signerFor: async (dataPath) => {
			const { iam, keysSecret, catalogUrl, signer } = settings;
			const credentials = iam
				? await identityFor(
						schema,
						{ bucket: signer.bucket, prefix: prefixOf(dataPath, signer.bucket) },
						{
							iam: storageIam(iam),
							store: catalogKeys(catalogUrl),
							seal: (text) => sealText(text, keysSecret),
							unseal: (text) => unsealText(text, keysSecret)
						}
					)
				: signer.fallback;

			return signWith(signer, credentials);
		},
		bucket: settings.signer.bucket,
		now: Date.now()
	});

	return result.ok ? Response.json(result.data) : Response.json({ message: result.message }, { status: result.status });
};
