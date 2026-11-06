package client

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"
)

// ptr returns a pointer to the value.
func ptr[T any](value T) *T {
	return &value
}

// The rows that the test server answers: a project, a schema and a table.
const (
	projectRow = `{"name":"sales","updated_at":"2026-01-01T00:00:00Z","updated_by":"terraform"}`
	schemaRow  = `{"name":"sales_orders","project":"sales","short_name":"orders","source":"bigquery","settings":null,` +
		`"claim":"preferred_username","encrypted":false,"updated_at":"2026-01-01T00:00:00Z","updated_by":"terraform"}`
	tableRow = `{"name":"sales.orders","schema":"sales_orders","strategy":"partitioned","n":3,"fallback":false,` +
		`"cache_ttl":60,"partitioning":null,"sort":["order_id"],"updated_at":"t","updated_by":"u"}`
)

func TestConfigOperations(t *testing.T) {
	wantProject := Project{Name: "sales", UpdatedAt: "2026-01-01T00:00:00Z", UpdatedBy: "terraform"}
	wantSchema := Schema{
		Name: "sales_orders", Project: "sales", ShortName: "orders", Source: "bigquery",
		Settings: json.RawMessage("null"), Claim: ptr("preferred_username"),
		UpdatedAt: "2026-01-01T00:00:00Z", UpdatedBy: "terraform",
	}
	wantTable := Table{
		Name: "sales.orders", Schema: "sales_orders", Strategy: "partitioned", N: ptr(3), CacheTTL: ptr(60),
		Partitioning: json.RawMessage("null"), Sort: []string{"order_id"}, UpdatedAt: "t", UpdatedBy: "u",
	}

	tests := []struct {
		name  string
		steps []step
		run   func(context.Context, *Client) (any, error)
		want  any
	}{
		{
			name:  "create a project",
			steps: []step{{method: "POST", uri: "/projects", contentProfile: "console", body: `{"name":"sales"}`, status: 201, response: "[" + projectRow + "]"}},
			run:   func(ctx context.Context, c *Client) (any, error) { return c.CreateProject(ctx, "sales") },
			want:  wantProject,
		},
		{
			name:  "get a project",
			steps: []step{{method: "GET", uri: "/projects?name=eq.sales", acceptProfile: "console", response: "[" + projectRow + "]"}},
			run:   func(ctx context.Context, c *Client) (any, error) { return c.GetProject(ctx, "sales") },
			want:  wantProject,
		},
		{
			name:  "delete a project",
			steps: []step{{method: "DELETE", uri: "/projects?name=eq.sales", contentProfile: "console", response: "[]"}},
			run:   func(ctx context.Context, c *Client) (any, error) { return nil, c.DeleteProject(ctx, "sales") },
		},
		{
			name: "create a schema from a project and a short name",
			steps: []step{{
				method: "POST", uri: "/schemas", contentProfile: "console",
				body:   `{"project":"sales","short_name":"orders","claim":"preferred_username","encrypted":false}`,
				status: 201, response: "[" + schemaRow + "]",
			}},
			run: func(ctx context.Context, c *Client) (any, error) {
				return c.CreateSchema(ctx, SchemaInput{Project: "sales", ShortName: "orders", Claim: ptr("preferred_username")})
			},
			want: wantSchema,
		},
		{
			name:  "get a schema by its physical name",
			steps: []step{{method: "GET", uri: "/schemas?name=eq.sales_orders", response: "[" + schemaRow + "]"}},
			run:   func(ctx context.Context, c *Client) (any, error) { return c.GetSchema(ctx, "sales_orders") },
			want:  wantSchema,
		},
		{
			name:  "get a schema by its parts",
			steps: []step{{method: "GET", uri: "/schemas?project=eq.sales&short_name=eq.orders", response: "[" + schemaRow + "]"}},
			run:   func(ctx context.Context, c *Client) (any, error) { return c.GetSchemaByParts(ctx, "sales", "orders") },
			want:  wantSchema,
		},
		{
			name: "update a schema and clear its settings",
			steps: []step{{
				method: "PATCH", uri: "/schemas?name=eq.sales_orders", contentProfile: "console",
				body:     `{"source":"bigquery","settings":null,"claim":"preferred_username","encrypted":false}`,
				response: "[" + schemaRow + "]",
			}},
			run: func(ctx context.Context, c *Client) (any, error) {
				return c.UpdateSchema(ctx, "sales_orders", SchemaUpdate{Source: "bigquery", Claim: ptr("preferred_username")})
			},
			want: wantSchema,
		},
		{
			name:  "delete a schema",
			steps: []step{{method: "DELETE", uri: "/schemas?name=eq.sales_orders", response: "[]"}},
			run:   func(ctx context.Context, c *Client) (any, error) { return nil, c.DeleteSchema(ctx, "sales_orders") },
		},
		{
			name: "create a table",
			steps: []step{{
				method: "POST", uri: "/tables", contentProfile: "console",
				body:   `{"name":"sales.orders","schema":"sales_orders","strategy":"partitioned","n":3,"fallback":false,"cache_ttl":60,"sort":["order_id"]}`,
				status: 201, response: "[" + tableRow + "]",
			}},
			run: func(ctx context.Context, c *Client) (any, error) {
				return c.CreateTable(ctx, TableInput{
					Name: "sales.orders", Schema: "sales_orders", Strategy: "partitioned", N: ptr(3),
					CacheTTL: ptr(60), Sort: []string{"order_id"},
				})
			},
			want: wantTable,
		},
		{
			name:  "get a table",
			steps: []step{{method: "GET", uri: "/tables?name=eq.sales.orders", response: "[" + tableRow + "]"}},
			run:   func(ctx context.Context, c *Client) (any, error) { return c.GetTable(ctx, "sales.orders") },
			want:  wantTable,
		},
		{
			name: "update a table and clear its cache time",
			steps: []step{{
				method: "PATCH", uri: "/tables?name=eq.sales.orders", contentProfile: "console",
				body:     `{"strategy":"partitioned","n":3,"fallback":false,"cache_ttl":null,"partitioning":null,"sort":["order_id"]}`,
				response: "[" + tableRow + "]",
			}},
			run: func(ctx context.Context, c *Client) (any, error) {
				return c.UpdateTable(ctx, "sales.orders", TableUpdate{Strategy: "partitioned", N: ptr(3), Sort: []string{"order_id"}})
			},
			want: wantTable,
		},
		{
			name:  "delete a table",
			steps: []step{{method: "DELETE", uri: "/tables?name=eq.sales.orders", response: "[]"}},
			run:   func(ctx context.Context, c *Client) (any, error) { return nil, c.DeleteTable(ctx, "sales.orders") },
		},
		{
			name: "list the row policies in order",
			steps: []step{{
				method: "GET", uri: "/rls?table_name=eq.sales.orders&order=id.asc",
				response: `[{"table_name":"sales.orders","column_name":"unit_id","unit_type":"unit"},` +
					`{"table_name":"sales.orders","column_name":"region_id","unit_type":"region"}]`,
			}},
			run: func(ctx context.Context, c *Client) (any, error) { return c.ListRLS(ctx, "sales.orders") },
			want: []RLSRule{
				{ColumnName: "unit_id", UnitType: "unit"},
				{ColumnName: "region_id", UnitType: "region"},
			},
		},
		{
			name: "replace the row policies with a delete and an insert of an array",
			steps: []step{
				{method: "DELETE", uri: "/rls?table_name=eq.sales.orders", response: "[]"},
				{
					method: "POST", uri: "/rls", contentProfile: "console", status: 201,
					body: `[{"table_name":"sales.orders","column_name":"unit_id","unit_type":"unit"},` +
						`{"table_name":"sales.orders","column_name":"region_id","unit_type":"region"}]`,
				},
			},
			run: func(ctx context.Context, c *Client) (any, error) {
				return nil, c.ReplaceRLS(ctx, "sales.orders", []RLSRule{
					{ColumnName: "unit_id", UnitType: "unit"},
					{ColumnName: "region_id", UnitType: "region"},
				})
			},
		},
		{
			name:  "replace the row policies with none only deletes",
			steps: []step{{method: "DELETE", uri: "/rls?table_name=eq.sales.orders", response: "[]"}},
			run:   func(ctx context.Context, c *Client) (any, error) { return nil, c.ReplaceRLS(ctx, "sales.orders", nil) },
		},
		{
			name: "read the health of a schema",
			steps: []step{{
				method: "GET", uri: "/health?name=eq.sales_orders", acceptProfile: "console",
				response: `[{"name":"sales_orders","schema_exists":true,"access_policy":true,"view_count":3}]`,
			}},
			run:  func(ctx context.Context, c *Client) (any, error) { return c.GetHealth(ctx, "sales_orders") },
			want: Health{Name: "sales_orders", SchemaExists: true, AccessPolicy: true, ViewCount: 3},
		},
		{
			name: "read the sync state of a table",
			steps: []step{{
				method: "GET", uri: "/synced?name=eq.sales.orders",
				response: `[{"schema":"sales_orders","name":"sales.orders","synced":true,"signature":"abc","partition_count":2}]`,
			}},
			run:  func(ctx context.Context, c *Client) (any, error) { return c.GetSyncState(ctx, "sales.orders") },
			want: TableSyncState{Schema: "sales_orders", Name: "sales.orders", Synced: true, Signature: ptr("abc"), PartitionCount: 2},
		},
		{
			name: "list the newest changes of a schema",
			steps: []step{{
				method: "GET", uri: "/config_history?order=changed_at.desc&limit=5&schema_name=eq.sales_orders",
				response: `[{"changed_at":"t","changed_by":null,"action":"insert","object":"tables","schema_name":"sales_orders","key":"sales.orders","old_row":null,"new_row":{"name":"sales.orders"}}]`,
			}},
			run: func(ctx context.Context, c *Client) (any, error) { return c.ListHistory(ctx, "sales_orders", 5) },
			want: []HistoryEntry{{
				ChangedAt: "t", Action: "insert", Object: "tables", SchemaName: "sales_orders", Key: "sales.orders",
				OldRow: json.RawMessage("null"), NewRow: json.RawMessage(`{"name":"sales.orders"}`),
			}},
		},
		{
			name:  "list the newest changes of every schema",
			steps: []step{{method: "GET", uri: "/config_history?order=changed_at.desc&limit=2", response: "[]"}},
			run:   func(ctx context.Context, c *Client) (any, error) { return c.ListHistory(ctx, "", 2) },
			want:  []HistoryEntry{},
		},
		{
			name:  "export the configuration",
			steps: []step{{method: "GET", uri: "/rpc/export_config", acceptProfile: "console", response: `{"projects":{}}`}},
			run:   func(ctx context.Context, c *Client) (any, error) { return c.ExportConfig(ctx) },
			want:  json.RawMessage(`{"projects":{}}`),
		},
		{
			name:  "read the sync configuration",
			steps: []step{{method: "GET", uri: "/rpc/sync_config", acceptProfile: "console", response: `{"schemas":{}}`}},
			run:   func(ctx context.Context, c *Client) (any, error) { return c.SyncConfig(ctx) },
			want:  json.RawMessage(`{"schemas":{}}`),
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := newStepClient(t, tt.steps)

			got, err := tt.run(context.Background(), c)
			if err != nil {
				t.Fatalf("call: %v", err)
			}

			if !reflect.DeepEqual(got, tt.want) {
				t.Errorf("result = %#v, want %#v", got, tt.want)
			}
		})
	}
}

func TestConfigOperationsReportErrors(t *testing.T) {
	tests := []struct {
		name     string
		steps    []step
		run      func(context.Context, *Client) error
		wantKind error
		wantText string
	}{
		{
			name:     "a project that does not exist",
			steps:    []step{{response: "[]"}},
			run:      func(ctx context.Context, c *Client) error { _, err := c.GetProject(ctx, "sales"); return err },
			wantKind: ErrNoRow,
		},
		{
			name:     "a delete that the database refuses because the project has schemas",
			steps:    []step{{status: 400, response: `{"code":"23001","message":"update or delete on table projects violates restrict"}`}},
			run:      func(ctx context.Context, c *Client) error { return c.DeleteProject(ctx, "sales") },
			wantKind: ErrInvalid,
		},
		{
			name:     "a schema that already exists",
			steps:    []step{{status: 409, response: `{"code":"23505","message":"duplicate key"}`}},
			run:      func(ctx context.Context, c *Client) error { _, err := c.CreateSchema(ctx, SchemaInput{}); return err },
			wantKind: ErrConflict,
		},
		{
			name:     "a write that the token may not do",
			steps:    []step{{status: 403, response: `{"code":"42501","message":"new row violates row-level security policy"}`}},
			run:      func(ctx context.Context, c *Client) error { _, err := c.CreateTable(ctx, TableInput{}); return err },
			wantKind: ErrForbidden,
		},
		{
			name:     "a write that gives back no row",
			steps:    []step{{status: 201, response: "[]"}},
			run:      func(ctx context.Context, c *Client) error { _, err := c.CreateProject(ctx, "sales"); return err },
			wantText: "the write to projects gave back 0 rows, want one",
		},
		{
			name:  "a row policy delete that fails",
			steps: []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			run: func(ctx context.Context, c *Client) error {
				return c.ReplaceRLS(ctx, "sales.orders", []RLSRule{{ColumnName: "a", UnitType: "b"}})
			},
			wantKind: ErrForbidden,
			wantText: "delete the row policies of sales.orders",
		},
		{
			name: "a row policy insert that fails after the delete",
			steps: []step{
				{response: "[]"},
				{status: 400, response: `{"code":"23514","message":"the schema has no claim"}`},
			},
			run: func(ctx context.Context, c *Client) error {
				return c.ReplaceRLS(ctx, "sales.orders", []RLSRule{{ColumnName: "a", UnitType: "b"}})
			},
			wantKind: ErrInvalid,
			wantText: "add the row policies of sales.orders",
		},
		{
			name:     "an export that fails",
			steps:    []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			run:      func(ctx context.Context, c *Client) error { _, err := c.ExportConfig(ctx); return err },
			wantKind: ErrForbidden,
			wantText: "export the configuration",
		},
		{
			name:     "a sync configuration that fails",
			steps:    []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			run:      func(ctx context.Context, c *Client) error { _, err := c.SyncConfig(ctx); return err },
			wantKind: ErrForbidden,
			wantText: "read the sync configuration",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := newStepClient(t, tt.steps)

			err := tt.run(context.Background(), c)
			if err == nil {
				t.Fatal("error = nil, want an error")
			}

			if tt.wantKind != nil && !errors.Is(err, tt.wantKind) {
				t.Errorf("error = %v, want one that wraps %v", err, tt.wantKind)
			}

			if tt.wantText != "" && !strings.Contains(err.Error(), tt.wantText) {
				t.Errorf("error = %q, want it to contain %q", err, tt.wantText)
			}
		})
	}
}
