import * as v from 'valibot';

export const Grant = v.object({
	schema: v.string(),
	tables: v.array(
		v.object({
			name: v.string(),
			files: v.array(v.object({ data: v.string(), delete: v.nullable(v.string()) }))
		})
	),
	expiresAt: v.number()
});

export const GrantMessage = v.object({ type: v.literal('grant'), grant: Grant });

export type Grant = v.InferOutput<typeof Grant>;

export type Status =
	| { type: 'ready' }
	| { type: 'applied'; schema: string; count: number; expiresAt: number }
	| { type: 'error'; message: string }
	| { type: 'csp'; directive: string; blocked: string }
	| { type: 'check'; check: string; value: number };
