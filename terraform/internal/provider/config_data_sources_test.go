package provider

import (
	"context"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/hashicorp/terraform-plugin-go/tftypes"
)

func TestProjectDataSource(t *testing.T) {
	c := newStepClient(t, []step{{method: "GET", uri: "/projects?name=eq.sales", profile: "console", response: "[" + projectRowJSON + "]"}})

	resp, got := readDataSource(t, newProjectDataSource(), c, projectDataModel{
		ID: types.StringNull(), Name: types.StringValue("sales"), UpdatedAt: types.StringNull(), UpdatedBy: types.StringNull(),
	})

	if len(got) != 0 {
		t.Fatalf("diagnostics = %+v, want none", got)
	}

	var state projectDataModel

	resp.State.Get(context.Background(), &state)

	if state.ID.ValueString() != "sales" || state.UpdatedBy.ValueString() != "terraform" {
		t.Errorf("state = %+v, want the row of sales", state)
	}
}

func TestSchemaDataSource(t *testing.T) {
	tests := []struct {
		name   string
		config schemaDataModel
		uri    string
	}{
		{
			name:   "by the physical name",
			config: schemaDataModel{Name: types.StringValue("sales_orders"), Project: types.StringNull(), ShortName: types.StringNull()},
			uri:    "/schemas?name=eq.sales_orders",
		},
		{
			name:   "by a project and a short name",
			config: schemaDataModel{Name: types.StringNull(), Project: types.StringValue("sales"), ShortName: types.StringValue("orders")},
			uri:    "/schemas?project=eq.sales&short_name=eq.orders",
		},
		{
			name:   "by a short name in the default project",
			config: schemaDataModel{Name: types.StringNull(), Project: types.StringNull(), ShortName: types.StringValue("orders")},
			uri:    "/schemas?project=eq.default&short_name=eq.orders",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := newStepClient(t, []step{{method: "GET", uri: tt.uri, response: "[" + schemaRowJSON + "]"}})
			config := tt.config
			config.ID, config.Source, config.Settings, config.Claim = types.StringNull(), types.StringNull(), types.StringNull(), types.StringNull()
			config.Encrypted, config.UpdatedAt, config.UpdatedBy = types.BoolNull(), types.StringNull(), types.StringNull()

			resp, got := readDataSource(t, newSchemaDataSource(), c, config)

			if len(got) != 0 {
				t.Fatalf("diagnostics = %+v, want none", got)
			}

			var state schemaDataModel

			resp.State.Get(context.Background(), &state)

			if state.Name.ValueString() != "sales_orders" || state.Claim.ValueString() != "preferred_username" || state.Settings.ValueString() != `{"b":1,"a":2}` {
				t.Errorf("state = %+v, want the schema row", state)
			}
		})
	}
}

func TestSchemaDataSourceReportsAMissingSchema(t *testing.T) {
	c := newStepClient(t, []step{{response: "[]"}})

	_, got := readDataSource(t, newSchemaDataSource(), c, schemaDataModel{
		ID: types.StringNull(), Name: types.StringValue("none"), Project: types.StringNull(), ShortName: types.StringNull(),
		Source: types.StringNull(), Settings: types.StringNull(), Claim: types.StringNull(), Encrypted: types.BoolNull(),
		UpdatedAt: types.StringNull(), UpdatedBy: types.StringNull(),
	})

	if len(got) != 1 || got[0].summary != "Couldn't read schema" {
		t.Fatalf("diagnostics = %+v, want the error of a missing schema", got)
	}
}

func TestTableDataSource(t *testing.T) {
	c := newStepClient(t, []step{
		{method: "GET", uri: "/tables?name=eq.sales.orders", response: "[" + tableRowJSON + "]"},
		{method: "GET", uri: "/rls?table_name=eq.sales.orders&order=id.asc", response: rlsRowsJSON},
	})

	resp, got := readDataSource(t, newTableDataSource(), c, tableModel{
		ID: types.StringNull(), Name: types.StringValue("sales.orders"), Schema: types.StringNull(), Strategy: types.StringNull(),
		N: types.Int64Null(), Fallback: types.BoolNull(), CacheTTL: types.Int64Null(), Partitioning: types.StringNull(),
		Sort: types.ListNull(types.StringType), UpdatedAt: types.StringNull(), UpdatedBy: types.StringNull(),
	})

	if len(got) != 0 {
		t.Fatalf("diagnostics = %+v, want none", got)
	}

	var state tableModel

	resp.State.Get(context.Background(), &state)

	if state.Schema.ValueString() != "sales_orders" || state.N.ValueInt64() != 3 || len(state.RLS) != 2 || state.RLS[0].ColumnName.ValueString() != "unit_id" {
		t.Errorf("state = %+v, want the table with its two row policies", state)
	}
}

func TestDocumentDataSources(t *testing.T) {
	tests := []struct {
		name     string
		source   func() datasource.DataSource
		uri      string
		response string
	}{
		{name: "export_config", source: newExportConfigDataSource, uri: "/rpc/export_config", response: `{"projects":{}}`},
		{name: "sync_config", source: newSyncConfigDataSource, uri: "/rpc/sync_config", response: `{"schemas":{}}`},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := newStepClient(t, []step{{method: "GET", uri: tt.uri, profile: "console", response: tt.response}})

			resp, got := readDataSource(t, tt.source(), c, documentModel{ID: types.StringNull(), Document: types.StringNull()})

			if len(got) != 0 {
				t.Fatalf("diagnostics = %+v, want none", got)
			}

			var state documentModel

			resp.State.Get(context.Background(), &state)

			if state.ID.ValueString() != tt.name || state.Document.ValueString() != tt.response {
				t.Errorf("state = %+v, want the document %s", state, tt.response)
			}
		})
	}
}

func TestSchemaHealthDataSource(t *testing.T) {
	c := newStepClient(t, []step{{uri: "/health?name=eq.sales_orders", response: `[{"name":"sales_orders","schema_exists":true,"access_policy":false,"view_count":4}]`}})

	resp, got := readDataSource(t, newSchemaHealthDataSource(), c, healthModel{
		ID: types.StringNull(), Name: types.StringValue("sales_orders"), SchemaExists: types.BoolNull(), AccessPolicy: types.BoolNull(), ViewCount: types.Int64Null(),
	})

	if len(got) != 0 {
		t.Fatalf("diagnostics = %+v, want none", got)
	}

	var state healthModel

	resp.State.Get(context.Background(), &state)

	if !state.SchemaExists.ValueBool() || state.AccessPolicy.ValueBool() || state.ViewCount.ValueInt64() != 4 {
		t.Errorf("state = %+v, want the schema but no access policy and 4 views", state)
	}
}

func TestTableSyncStateDataSource(t *testing.T) {
	tests := []struct {
		name          string
		response      string
		wantSignature types.String
	}{
		{name: "a synced table", response: `[{"schema":"s","name":"sales.orders","synced":true,"signature":"abc","partition_count":2}]`, wantSignature: types.StringValue("abc")},
		{name: "a table that was never synced", response: `[{"schema":"s","name":"sales.orders","synced":false,"signature":null,"partition_count":0}]`, wantSignature: types.StringNull()},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := newStepClient(t, []step{{uri: "/synced?name=eq.sales.orders", response: tt.response}})

			resp, got := readDataSource(t, newTableSyncStateDataSource(), c, syncStateModel{
				ID: types.StringNull(), Name: types.StringValue("sales.orders"), Schema: types.StringNull(), Synced: types.BoolNull(),
				Signature: types.StringNull(), PartitionCount: types.Int64Null(),
			})

			if len(got) != 0 {
				t.Fatalf("diagnostics = %+v, want none", got)
			}

			var state syncStateModel

			resp.State.Get(context.Background(), &state)

			if !state.Signature.Equal(tt.wantSignature) {
				t.Errorf("signature = %v, want %v", state.Signature, tt.wantSignature)
			}
		})
	}
}

func TestConfigHistoryDataSource(t *testing.T) {
	entry := `[{"changed_at":"t","changed_by":null,"action":"insert","object":"tables","schema_name":"s","key":"k","old_row":null,"new_row":{"a":1}}]`

	tests := []struct {
		name   string
		schema types.String
		limit  types.Int64
		uri    string
	}{
		{name: "the default limit", schema: types.StringNull(), limit: types.Int64Null(), uri: "/config_history?order=changed_at.desc&limit=100"},
		{name: "a schema and a limit", schema: types.StringValue("s"), limit: types.Int64Value(5), uri: "/config_history?order=changed_at.desc&limit=5&schema_name=eq.s"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := newStepClient(t, []step{{uri: tt.uri, response: entry}})

			resp, got := readDataSource(t, newConfigHistoryDataSource(), c, historyModel{ID: types.StringNull(), Schema: tt.schema, Limit: tt.limit})

			if len(got) != 0 {
				t.Fatalf("diagnostics = %+v, want none", got)
			}

			var state historyModel

			resp.State.Get(context.Background(), &state)

			if len(state.Entries) != 1 || state.Entries[0].NewRow.ValueString() != `{"a":1}` || !state.Entries[0].OldRow.IsNull() || !state.Entries[0].ChangedBy.IsNull() {
				t.Errorf("entries = %+v, want one insert with a JSON new row", state.Entries)
			}
		})
	}
}

func TestDataSourceWithoutAClientFails(t *testing.T) {
	source := newProjectDataSource()
	s := dataSourceSchema(t, source)
	raw := tftypes.NewValue(s.Type().TerraformType(context.Background()), nil)
	resp := datasource.ReadResponse{State: tfsdk.State{Schema: s, Raw: raw}}

	source.Read(context.Background(), datasource.ReadRequest{Config: tfsdk.Config{Schema: s, Raw: raw}}, &resp)

	if got := summaries(diagnosticsOf(resp.Diagnostics)); len(got) != 1 || got[0] != "Unconfigured provider" {
		t.Errorf("diagnostics = %v, want the error of a missing client", got)
	}
}
