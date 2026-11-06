package provider

import (
	"context"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"terraform-provider-data-proxy/internal/client"
)

// The rows that the test server gives for the table sales.orders and its row policies.
const (
	tableRowJSON = `{"name":"sales.orders","schema":"sales_orders","strategy":"partitioned","n":3,"fallback":false,` +
		`"cache_ttl":60,"partitioning":null,"sort":["order_id"],"updated_at":"2026-01-01T00:00:00Z","updated_by":"terraform"}`
	rlsRowsJSON = `[{"table_name":"sales.orders","column_name":"unit_id","unit_type":"unit"},` +
		`{"table_name":"sales.orders","column_name":"region_id","unit_type":"region"}]`
)

// newTestTable returns the resource with a client that the steps drive, and no wait between reads.
func newTestTable(t *testing.T, steps []step) resource.Resource {
	t.Helper()

	res := newTableResource()
	res.(*tableResource).backoff = client.Backoff{}

	return configured(t, res, newStepClient(t, steps))
}

// tableConfig returns the model of the table sales.orders with the given row policies.
func tableConfig(t *testing.T, rules ...rlsModel) tableModel {
	t.Helper()

	sort, diagnostics := types.ListValueFrom(context.Background(), types.StringType, []string{"order_id"})
	if diagnostics.HasError() {
		t.Fatalf("build the sort: %v", diagnostics)
	}

	return tableModel{
		ID:           types.StringUnknown(),
		Name:         types.StringValue("sales.orders"),
		Schema:       types.StringValue("sales_orders"),
		Strategy:     types.StringValue("partitioned"),
		N:            types.Int64Value(3),
		Fallback:     types.BoolValue(false),
		CacheTTL:     types.Int64Value(60),
		Partitioning: types.StringNull(),
		Sort:         sort,
		RLS:          rules,
		UpdatedAt:    types.StringUnknown(),
		UpdatedBy:    types.StringUnknown(),
	}
}

// ruleModel returns a row policy block.
func ruleModel(column, unit string) rlsModel {
	return rlsModel{ColumnName: types.StringValue(column), UnitType: types.StringValue(unit)}
}

// twoRules are the row policies of the tests, in order.
func twoRules() []rlsModel {
	return []rlsModel{ruleModel("unit_id", "unit"), ruleModel("region_id", "region")}
}

func TestTableCreate(t *testing.T) {
	createStep := step{
		method: "POST", uri: "/tables", profile: "console", status: 201, response: "[" + tableRowJSON + "]",
		body: `{"name":"sales.orders","schema":"sales_orders","strategy":"partitioned","n":3,"fallback":false,"cache_ttl":60,"sort":["order_id"]}`,
	}
	rulesStep := step{
		method: "POST", uri: "/rls", profile: "console", status: 201,
		body: `[{"table_name":"sales.orders","column_name":"unit_id","unit_type":"unit"},{"table_name":"sales.orders","column_name":"region_id","unit_type":"region"}]`,
	}
	deleteRules := step{method: "DELETE", uri: "/rls?table_name=eq.sales.orders", response: "[]"}

	tests := []struct {
		name        string
		rules       []rlsModel
		steps       []step
		wantSummary string
		wantRules   int
	}{
		{name: "adds a table without row policies", steps: []step{createStep}},
		{name: "adds the table, then its row policies in order", rules: twoRules(), steps: []step{createStep, deleteRules, rulesStep}, wantRules: 2},
		{
			name:        "keeps the table when the row policies fail",
			rules:       twoRules(),
			steps:       []step{createStep, deleteRules, {status: 400, response: `{"code":"23514","message":"the schema has no claim"}`}},
			wantSummary: "Couldn't add the row policies of the table",
			wantRules:   2,
		},
		{
			name:        "reports a refused write",
			steps:       []step{{status: 409, response: `{"code":"23505","message":"duplicate key"}`}},
			wantSummary: "Couldn't create the table",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			res := newTestTable(t, tt.steps)
			s := resourceSchema(t, res)
			resp := resource.CreateResponse{State: nullState(s)}

			res.Create(context.Background(), resource.CreateRequest{Plan: planOf(t, s, tableConfig(t, tt.rules...))}, &resp)

			got := summaries(diagnosticsOf(resp.Diagnostics))

			switch {
			case tt.wantSummary != "" && (len(got) != 1 || got[0] != tt.wantSummary):
				t.Fatalf("diagnostics = %v, want %q", got, tt.wantSummary)
			case tt.wantSummary == "" && len(got) != 0:
				t.Fatalf("diagnostics = %v, want none", got)
			}

			if tt.wantSummary == "Couldn't create the table" {
				return
			}

			var state tableModel

			resp.State.Get(context.Background(), &state)

			if state.ID.ValueString() != "sales.orders" || len(state.RLS) != tt.wantRules {
				t.Errorf("id = %q, rules = %d, want sales.orders and %d rules", state.ID.ValueString(), len(state.RLS), tt.wantRules)
			}
		})
	}
}

func TestTableRead(t *testing.T) {
	tests := []struct {
		name        string
		steps       []step
		wantRemoved bool
		wantSummary string
	}{
		{
			name: "reads the row and its row policies",
			steps: []step{
				{method: "GET", uri: "/tables?name=eq.sales.orders", response: "[" + tableRowJSON + "]"},
				{method: "GET", uri: "/rls?table_name=eq.sales.orders&order=id.asc", response: rlsRowsJSON},
			},
		},
		{name: "drops a table that no longer exists", steps: []step{{response: "[]"}}, wantRemoved: true},
		{name: "reports a read of the table that fails", steps: []step{{status: 403, response: `{"code":"42501","message":"denied"}`}}, wantSummary: "Couldn't read the table"},
		{
			name: "reports a read of the row policies that fails",
			steps: []step{
				{response: "[" + tableRowJSON + "]"},
				{status: 403, response: `{"code":"42501","message":"denied"}`},
			},
			wantSummary: "Couldn't read the row policies of the table",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			res := newTestTable(t, tt.steps)
			s := resourceSchema(t, res)
			state := stateOf(t, s, tableConfig(t))
			resp := resource.ReadResponse{State: state}

			res.Read(context.Background(), resource.ReadRequest{State: state}, &resp)

			got := summaries(diagnosticsOf(resp.Diagnostics))

			switch {
			case tt.wantSummary != "":
				if len(got) != 1 || got[0] != tt.wantSummary {
					t.Fatalf("diagnostics = %v, want %q", got, tt.wantSummary)
				}
			case tt.wantRemoved:
				if !resp.State.Raw.IsNull() {
					t.Errorf("state = %v, want it removed", resp.State.Raw)
				}
			default:
				var read tableModel

				resp.State.Get(context.Background(), &read)

				if len(read.RLS) != 2 || read.RLS[1].ColumnName.ValueString() != "region_id" || read.CacheTTL.ValueInt64() != 60 {
					t.Errorf("state = %+v, want two ordered row policies and the cache time", read)
				}
			}
		})
	}
}

func TestTableUpdate(t *testing.T) {
	patch := step{
		method: "PATCH", uri: "/tables?name=eq.sales.orders", profile: "console", response: "[" + tableRowJSON + "]",
		body: `{"strategy":"partitioned","n":3,"fallback":false,"cache_ttl":60,"partitioning":null,"sort":["order_id"]}`,
	}
	deleteRules := step{method: "DELETE", uri: "/rls?table_name=eq.sales.orders", response: "[]"}

	tests := []struct {
		name        string
		plan        []rlsModel
		steps       []step
		wantSummary string
		wantRules   int
	}{
		{name: "leaves the row policies alone when they are the same", plan: twoRules(), steps: []step{patch}, wantRules: 2},
		{
			name:      "replaces the row policies in the new order",
			plan:      []rlsModel{ruleModel("region_id", "region"), ruleModel("unit_id", "unit")},
			steps:     []step{patch, deleteRules, {method: "POST", uri: "/rls", status: 201}},
			wantRules: 2,
		},
		{name: "removes every row policy", plan: nil, steps: []step{patch, deleteRules}, wantRules: 0},
		{
			name:        "keeps the old row policies in the state when the replace fails",
			plan:        []rlsModel{ruleModel("unit_id", "unit")},
			steps:       []step{patch, {status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantSummary: "Couldn't replace the row policies of the table",
			wantRules:   2,
		},
		{name: "reports a refused update", plan: twoRules(), steps: []step{{status: 403, response: `{"code":"42501","message":"denied"}`}}, wantSummary: "Couldn't update the table"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			res := newTestTable(t, tt.steps)
			s := resourceSchema(t, res)
			state := stateOf(t, s, withName(tableConfig(t, twoRules()...)))
			plan := planOf(t, s, withName(tableConfig(t, tt.plan...)))
			resp := resource.UpdateResponse{State: state}

			res.Update(context.Background(), resource.UpdateRequest{Plan: plan, State: state}, &resp)

			got := summaries(diagnosticsOf(resp.Diagnostics))

			switch {
			case tt.wantSummary != "" && (len(got) != 1 || got[0] != tt.wantSummary):
				t.Fatalf("diagnostics = %v, want %q", got, tt.wantSummary)
			case tt.wantSummary == "" && len(got) != 0:
				t.Fatalf("diagnostics = %v, want none", got)
			}

			if tt.wantSummary == "Couldn't update the table" {
				return
			}

			var saved tableModel

			resp.State.Get(context.Background(), &saved)

			if len(saved.RLS) != tt.wantRules {
				t.Errorf("row policies in the state = %d, want %d", len(saved.RLS), tt.wantRules)
			}
		})
	}
}

// withName fills the computed id of a model, as a saved table has it.
func withName(model tableModel) tableModel {
	model.ID = types.StringValue("sales.orders")
	model.UpdatedAt = types.StringValue("t")
	model.UpdatedBy = types.StringValue("u")

	return model
}

func TestTableDelete(t *testing.T) {
	tests := []struct {
		name        string
		steps       []step
		wantSummary string
	}{
		{name: "removes the table", steps: []step{{method: "DELETE", uri: "/tables?name=eq.sales.orders", profile: "console", response: "[]"}}},
		{name: "reports a refused delete", steps: []step{{status: 403, response: `{"code":"42501","message":"denied"}`}}, wantSummary: "Couldn't delete the table"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			res := newTestTable(t, tt.steps)
			s := resourceSchema(t, res)
			state := stateOf(t, s, withName(tableConfig(t)))
			resp := resource.DeleteResponse{State: state}

			res.Delete(context.Background(), resource.DeleteRequest{State: state}, &resp)

			got := summaries(diagnosticsOf(resp.Diagnostics))

			switch {
			case tt.wantSummary != "" && (len(got) != 1 || got[0] != tt.wantSummary):
				t.Fatalf("diagnostics = %v, want %q", got, tt.wantSummary)
			case tt.wantSummary == "" && len(got) != 0:
				t.Fatalf("diagnostics = %v, want none", got)
			}
		})
	}
}

func TestTableValidateConfig(t *testing.T) {
	tests := []struct {
		name     string
		strategy types.String
		n        types.Int64
		fallback types.Bool
		want     []string
	}{
		{name: "a partitioned table with n and fallback", strategy: types.StringValue("partitioned"), n: types.Int64Value(3), fallback: types.BoolValue(true)},
		{name: "a full table without n and fallback", strategy: types.StringValue("full"), n: types.Int64Null(), fallback: types.BoolValue(false)},
		{name: "a table without a strategy and with n", strategy: types.StringNull(), n: types.Int64Value(3), fallback: types.BoolValue(false), want: []string{"Invalid n"}},
		{name: "a full table with n and fallback", strategy: types.StringValue("full"), n: types.Int64Value(3), fallback: types.BoolValue(true), want: []string{"Invalid n", "Invalid fallback"}},
		{name: "an unknown strategy", strategy: types.StringUnknown(), n: types.Int64Value(3), fallback: types.BoolValue(true)},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			res := newTestTable(t, nil)
			s := resourceSchema(t, res)
			model := tableConfig(t)
			model.Strategy, model.N, model.Fallback = tt.strategy, tt.n, tt.fallback
			config := tfsdk.Config{Schema: s, Raw: planOf(t, s, model).Raw}
			resp := resource.ValidateConfigResponse{}

			res.(resource.ResourceWithValidateConfig).ValidateConfig(context.Background(), resource.ValidateConfigRequest{Config: config}, &resp)

			got := summaries(diagnosticsOf(resp.Diagnostics))
			if strings.Join(got, ",") != strings.Join(tt.want, ",") {
				t.Errorf("diagnostics = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestTableImportReadsByName(t *testing.T) {
	res := newTestTable(t, nil)
	s := resourceSchema(t, res)
	resp := resource.ImportStateResponse{State: nullState(s)}

	res.(resource.ResourceWithImportState).ImportState(context.Background(), resource.ImportStateRequest{ID: "sales.orders"}, &resp)

	var state tableModel

	resp.State.Get(context.Background(), &state)

	if state.Name.ValueString() != "sales.orders" {
		t.Errorf("name = %q, want sales.orders", state.Name.ValueString())
	}
}

func TestSameRules(t *testing.T) {
	a := []client.RLSRule{{ColumnName: "a", UnitType: "x"}, {ColumnName: "b", UnitType: "y"}}
	tests := []struct {
		name  string
		right []client.RLSRule
		want  bool
	}{
		{name: "the same rules", right: []client.RLSRule{{ColumnName: "a", UnitType: "x"}, {ColumnName: "b", UnitType: "y"}}, want: true},
		{name: "the same rules in another order", right: []client.RLSRule{{ColumnName: "b", UnitType: "y"}, {ColumnName: "a", UnitType: "x"}}},
		{name: "fewer rules", right: a[:1]},
		{name: "no rules", right: nil},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := sameRules(a, tt.right); got != tt.want {
				t.Errorf("sameRules = %t, want %t", got, tt.want)
			}
		})
	}
}
