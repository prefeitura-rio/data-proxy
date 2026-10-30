import * as v from 'valibot';

const nullableString = v.nullable(v.string());
const fields = v.nullable(v.record(v.string(), v.unknown()));

export const Overview = v.object({
	name: v.string(),
	last_run_id: nullableString,
	last_run_status: nullableString,
	last_run_at: nullableString,
	tables: v.number(),
	synced_tables: v.number(),
	runs_24h: v.number(),
	failed_runs_24h: v.number(),
	errors_24h: v.number()
});

export const Run = v.object({
	id: v.string(),
	status: v.string(),
	queue_name: nullableString,
	schedule_name: nullableString,
	started_by: nullableString,
	created_at: nullableString,
	started_at: nullableString,
	completed_at: nullableString,
	task_count: v.number(),
	failed_task_count: v.number()
});

export const Task = v.object({
	id: v.string(),
	run_id: v.string(),
	name: v.string(),
	status: v.string(),
	queue_name: nullableString,
	schema_name: nullableString,
	created_at: nullableString,
	completed_at: nullableString
});

export const Step = v.object({
	workflow_id: v.string(),
	function_id: v.number(),
	function_name: v.string(),
	child_workflow_id: nullableString,
	run_id: v.string(),
	failed: v.boolean(),
	started_at: nullableString,
	completed_at: nullableString
});

export const HistoryRow = v.object({
	id: v.number(),
	changed_at: v.string(),
	changed_by: nullableString,
	action: v.string(),
	object: v.string(),
	schema_name: v.string(),
	key: v.string()
});

export const ImportChange = v.object({
	object: v.string(),
	schema_name: v.string(),
	key: v.string(),
	action: v.string()
});

export const Queue = v.object({
	name: v.string(),
	concurrency: v.nullable(v.number()),
	worker_concurrency: v.nullable(v.number()),
	enqueued: v.number(),
	pending: v.number(),
	delayed: v.number()
});

export const Schedule = v.object({
	name: v.string(),
	workflow_name: v.string(),
	schedule: v.string(),
	status: v.string(),
	queue_name: nullableString,
	timezone: nullableString,
	last_fired_at: nullableString,
	schema: v.string()
});

export const ErrorRow = v.object({
	id: v.number(),
	reason: v.string(),
	fields: v.record(v.string(), v.unknown()),
	recorded_at: v.string()
});

export const LogLine = v.object({
	workflow_id: v.string(),
	position: v.number(),
	logged_at: v.string(),
	level: v.string(),
	message: v.string(),
	fields,
	schema_name: nullableString,
	table_name: nullableString,
	run_id: v.string()
});

export const SchemaRow = v.object({
	name: v.string(),
	source: v.string(),
	settings: fields,
	claim: nullableString,
	encrypted: v.boolean(),
	updated_at: v.string(),
	updated_by: v.string()
});

export const SchemaName = v.pick(SchemaRow, ['name']);
export const SchemaFlags = v.pick(SchemaRow, ['name', 'encrypted']);

export const TableRow = v.object({
	name: v.string(),
	schema: v.string(),
	strategy: v.picklist(['full', 'partitioned']),
	n: v.nullable(v.number()),
	fallback: v.boolean(),
	cache_ttl: v.nullable(v.number()),
	partitioning: v.nullable(v.array(v.unknown())),
	sort: v.nullable(v.array(v.string())),
	updated_at: v.string(),
	updated_by: v.string()
});

export const TableName = v.pick(TableRow, ['name', 'schema']);

export const RlsRow = v.object({
	id: v.number(),
	table_name: v.string(),
	column_name: v.string(),
	unit_type: v.string(),
	updated_at: v.string(),
	updated_by: v.string()
});

export const Health = v.object({
	name: v.string(),
	schema_exists: v.boolean(),
	access_policy: v.boolean(),
	view_count: v.number()
});

export const Synced = v.object({
	schema: v.string(),
	name: v.string(),
	synced: v.boolean(),
	signature: nullableString,
	partition_count: v.number()
});

export const PolicyRow = v.object({
	subject: v.string(),
	unit_type: nullableString,
	unit_id: nullableString
});

export const AccessLogRow = v.object({
	...PolicyRow.entries,
	action: v.string(),
	changed_at: v.string()
});

export const Grant = v.object({
	schema: v.string(),
	tables: v.array(
		v.object({
			name: v.string(),
			files: v.array(v.object({ data: v.string(), delete: nullableString }))
		})
	),
	expiresAt: v.number()
});

export type Overview = v.InferOutput<typeof Overview>;
export type Run = v.InferOutput<typeof Run>;
export type Task = v.InferOutput<typeof Task>;
export type Queue = v.InferOutput<typeof Queue>;
export type Schedule = v.InferOutput<typeof Schedule>;
export type ErrorRow = v.InferOutput<typeof ErrorRow>;
export type LogLine = v.InferOutput<typeof LogLine>;
export type SchemaRow = v.InferOutput<typeof SchemaRow>;
export type TableRow = v.InferOutput<typeof TableRow>;
export type RlsRow = v.InferOutput<typeof RlsRow>;
export type Health = v.InferOutput<typeof Health>;
export type Synced = v.InferOutput<typeof Synced>;
export type HistoryRow = v.InferOutput<typeof HistoryRow>;
export type ImportChange = v.InferOutput<typeof ImportChange>;
export type PolicyRow = v.InferOutput<typeof PolicyRow>;
export type AccessLogRow = v.InferOutput<typeof AccessLogRow>;
export type Grant = v.InferOutput<typeof Grant>;
