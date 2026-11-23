package provider_test

import (
	"context"
	"data-proxy/internal/client"
	"data-proxy/internal/provider"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-framework/types"
	acc "github.com/hashicorp/terraform-plugin-testing/helper/resource"
)

// The rows that the test server gives for the table sales.orders and its row policies.
const (
	tableRowJSON = `{"name":"sales.orders","schema":"sales_orders","strategy":"partitioned","n":3,"fallback":false,` +
		`"cache_ttl":60,"partitioning":null,"sort":["order_id"],"updated_at":"2026-01-01T00:00:00Z","updated_by":"terraform"}`
	rlsRowsJSON = `[{"table_name":"sales.orders","groups":[{"conditions":[{"column":"unit_id","unit_type":"unit"}]},` +
		`{"conditions":[{"column":"region_id","unit_type":"region"}]}]}]`
)

// newTestTable returns the resource with a client that the steps drive, and no wait between reads.
func newTestTable(t *testing.T, steps []step) resource.Resource {
	t.Helper()

	res := provider.TableResourceWithNoRetry()

	return configured(t, res, newStepClient(t, steps))
}

// tableConfig returns the model of the table sales.orders with the given row policies.
func tableConfig(t *testing.T, rules ...provider.RlsModel) provider.TableModel {
	t.Helper()

	sort, diagnostics := types.ListValueFrom(context.Background(), types.StringType, []string{"order_id"})
	if diagnostics.HasError() {
		t.Fatalf("build the sort: %v", diagnostics)
	}

	return provider.TableModel{
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

// ruleModel returns an rls block in the flat form, for one condition.
func ruleModel(column, unit string) provider.RlsModel {
	return provider.EncodeRule(rule(column, unit))
}

// groupModel returns an rls block with one condition block for each pair of a column and a unit type.
func groupModel(pairs ...string) provider.RlsModel {
	return provider.EncodeRule(rule(pairs...))
}

// rule returns a client rule with one condition for each pair of a column and a unit type.
func rule(pairs ...string) client.RLSRule {
	conditions := make([]client.RLSCondition, 0, len(pairs)/2)
	for pair := range slices.Chunk(pairs, 2) {
		if len(pair) == 2 {
			conditions = append(conditions, client.RLSCondition{ColumnName: pair[0], UnitType: pair[1]})
		}
	}

	return client.RLSRule{Conditions: conditions}
}

func TestRLSRulesKeepTheOrderOfTheBlocks(t *testing.T) {
	t.Parallel()

	model := provider.TableModel{
		RLS: []provider.RlsModel{
			groupModel("region_id", "region", "unit_id", "unit"),
			ruleModel("area", "area"),
			groupModel("a", "x", "b", "y"),
		},
	}

	got := provider.RlsRules(model)

	want := []client.RLSRule{
		rule("region_id", "region", "unit_id", "unit"),
		rule("area", "area"),
		rule("a", "x", "b", "y"),
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("rlsRules() = %+v, want %+v", got, want)
	}
}

func TestApplyTableRowWritesOneConditionFlatAndTwoAsConditionBlocks(t *testing.T) {
	t.Parallel()

	var model provider.TableModel

	diagnostics := provider.ApplyTableRow(t.Context(), &model, client.Table{Name: "sales.orders"}, []client.RLSRule{
		rule("region_id", "region", "unit_id", "unit"),
		rule("area", "area"),
	})
	if diagnostics.HasError() {
		t.Fatalf("applyTableRow() = %v", diagnostics)
	}

	if len(model.RLS) != 2 || len(model.RLS[0].Conditions) != 2 || len(model.RLS[1].Conditions) != 0 {
		t.Fatalf("RLS = %+v, want one block of two conditions, then one flat block", model.RLS)
	}

	if !model.RLS[0].ColumnName.IsNull() || !model.RLS[0].UnitType.IsNull() {
		t.Errorf("a block of two conditions sets column_name = %v, want null", model.RLS[0].ColumnName)
	}

	if got := model.RLS[0].Conditions[1].ColumnName.ValueString(); got != "unit_id" {
		t.Errorf("second condition = %q, want unit_id", got)
	}

	if got := model.RLS[1].ColumnName.ValueString(); got != "area" {
		t.Errorf("flat block = %q, want area", got)
	}
}

func TestRLSRuleJSONHasNoNumber(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		rule client.RLSRule
		want string
	}{
		{name: "a condition", rule: rule("a", "x"), want: `{"conditions":[{"column":"a","unit_type":"x"}]}`},
		{
			name: "a group",
			rule: rule("a", "x", "b", "y"),
			want: `{"conditions":[{"column":"a","unit_type":"x"},{"column":"b","unit_type":"y"}]}`,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()

			got, err := json.Marshal(test.rule)
			if err != nil {
				t.Fatal(err)
			}

			if string(got) != test.want {
				t.Errorf("json = %s, want %s", got, test.want)
			}
		})
	}
}

// twoRules are the row policies of the tests, in order.
func twoRules() []provider.RlsModel {
	return []provider.RlsModel{ruleModel("unit_id", "unit"), ruleModel("region_id", "region")}
}

func TestTableCreate(t *testing.T) {
	t.Parallel()

	createStep := step{
		method:   "POST",
		uri:      "/tables",
		profile:  "console",
		status:   201,
		response: "[" + tableRowJSON + "]",
		body: `{"name":"sales.orders","schema":"sales_orders","strategy":"partitioned","n":3,"fallback":false,` +
			`"cache_ttl":60,"sort":["order_id"]}`,
	}
	rulesStep := step{
		method:   "POST",
		uri:      "/rpc/replace_rls",
		profile:  "console",
		response: nullJSON,
		body: `{"table_name":"sales.orders","groups":[{"conditions":[{"column":"unit_id","unit_type":"unit"}]},` +
			`{"conditions":[{"column":"region_id","unit_type":"region"}]}]}`,
	}

	tests := []struct {
		name        string
		rules       []provider.RlsModel
		steps       []step
		wantSummary string
		wantRules   int
	}{
		{name: "adds a table without row policies", steps: []step{createStep}},
		{
			name:      "adds the table, then its row policies in order",
			rules:     twoRules(),
			steps:     []step{createStep, rulesStep},
			wantRules: 2,
		},
		{
			name:        "keeps the table when the row policies fail",
			rules:       twoRules(),
			steps:       []step{createStep, {status: 400, response: `{"code":"23514","message":"the schema has no claim"}`}},
			wantSummary: "Couldn't add the row policies of the table",
			wantRules:   2,
		},
		{
			name:        "reports a refused write",
			steps:       []step{{status: 409, response: `{"code":"23505","message":"duplicate key"}`}},
			wantSummary: "Couldn't create the table",
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			res := newTestTable(t, testCase.steps)
			s := resourceSchema(t, res)
			resp := resource.CreateResponse{State: nullState(s)}

			res.Create(
				context.Background(),
				resource.CreateRequest{Plan: makePlan(t, s, tableConfig(t, testCase.rules...))},
				&resp,
			)

			got := summaries(collectDiagnostics(resp.Diagnostics))

			switch {
			case testCase.wantSummary != "" && (len(got) != 1 || got[0] != testCase.wantSummary):
				t.Fatalf("diagnostics = %v, want %q", got, testCase.wantSummary)
			case testCase.wantSummary == "" && len(got) != 0:
				t.Fatalf("diagnostics = %v, want none", got)
			}

			if testCase.wantSummary == "Couldn't create the table" {
				return
			}

			var state provider.TableModel

			resp.State.Get(context.Background(), &state)

			if state.ID.ValueString() != "sales.orders" || len(state.RLS) != testCase.wantRules {
				t.Errorf(
					"id = %q, rules = %d, want sales.orders and %d rules",
					state.ID.ValueString(),
					len(state.RLS),
					testCase.wantRules,
				)
			}
		})
	}
}

func TestTableRead(t *testing.T) {
	t.Parallel()

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
				{method: "POST", uri: "/rpc/rules", response: rlsRowsJSON},
			},
		},
		{name: "drops a table that no longer exists", steps: []step{{response: "[]"}}, wantRemoved: true},
		{
			name:        "reports a read of the table that fails",
			steps:       []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantSummary: "Couldn't read the table",
		},
		{
			name: "reports a read of the row policies that fails",
			steps: []step{
				{response: "[" + tableRowJSON + "]"},
				{status: 403, response: `{"code":"42501","message":"denied"}`},
			},
			wantSummary: "Couldn't read the row policies of the table",
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			res := newTestTable(t, testCase.steps)
			s := resourceSchema(t, res)
			state := makeState(t, s, tableConfig(t))
			resp := resource.ReadResponse{State: state}

			res.Read(context.Background(), resource.ReadRequest{State: state}, &resp)

			got := summaries(collectDiagnostics(resp.Diagnostics))

			switch {
			case testCase.wantSummary != "":
				if len(got) != 1 || got[0] != testCase.wantSummary {
					t.Fatalf("diagnostics = %v, want %q", got, testCase.wantSummary)
				}
			case testCase.wantRemoved:
				if !resp.State.Raw.IsNull() {
					t.Errorf("state = %v, want it removed", resp.State.Raw)
				}
			default:
				var read provider.TableModel

				resp.State.Get(context.Background(), &read)

				if len(read.RLS) != 2 || read.RLS[1].ColumnName.ValueString() != "region_id" || read.CacheTTL.ValueInt64() != 60 {
					t.Errorf("state = %+v, want two ordered row policies and the cache time", read)
				}
			}
		})
	}
}

func TestTableUpdate(t *testing.T) {
	t.Parallel()

	patch := step{
		method: "PATCH", uri: "/tables?name=eq.sales.orders", profile: "console", response: "[" + tableRowJSON + "]",
		body: `{"strategy":"partitioned","n":3,"fallback":false,"cache_ttl":60,"partitioning":null,"sort":["order_id"]}`,
	}
	replaceRules := step{method: "POST", uri: "/rpc/replace_rls", response: "null"}

	tests := []struct {
		name        string
		plan        []provider.RlsModel
		steps       []step
		wantSummary string
		wantRules   int
	}{
		{name: "leaves the row policies alone when they are the same", plan: twoRules(), steps: []step{patch}, wantRules: 2},
		{
			name:      "replaces the row policies in the new order",
			plan:      []provider.RlsModel{ruleModel("region_id", "region"), ruleModel("unit_id", "unit")},
			steps:     []step{patch, replaceRules},
			wantRules: 2,
		},
		{name: "removes every row policy", plan: nil, steps: []step{patch, replaceRules}, wantRules: 0},
		{
			name:        "keeps the old row policies in the state when the replace fails",
			plan:        []provider.RlsModel{ruleModel("unit_id", "unit")},
			steps:       []step{patch, {status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantSummary: "Couldn't replace the row policies of the table",
			wantRules:   2,
		},
		{
			name:        "reports a refused update",
			plan:        twoRules(),
			steps:       []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantSummary: "Couldn't update the table",
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			res := newTestTable(t, testCase.steps)
			s := resourceSchema(t, res)
			state := makeState(t, s, withName(tableConfig(t, twoRules()...)))
			plan := makePlan(t, s, withName(tableConfig(t, testCase.plan...)))
			resp := resource.UpdateResponse{State: state}

			res.Update(context.Background(), resource.UpdateRequest{Plan: plan, State: state}, &resp)

			got := summaries(collectDiagnostics(resp.Diagnostics))

			switch {
			case testCase.wantSummary != "" && (len(got) != 1 || got[0] != testCase.wantSummary):
				t.Fatalf("diagnostics = %v, want %q", got, testCase.wantSummary)
			case testCase.wantSummary == "" && len(got) != 0:
				t.Fatalf("diagnostics = %v, want none", got)
			}

			if testCase.wantSummary == "Couldn't update the table" {
				return
			}

			var saved provider.TableModel

			resp.State.Get(context.Background(), &saved)

			if len(saved.RLS) != testCase.wantRules {
				t.Errorf("row policies in the state = %d, want %d", len(saved.RLS), testCase.wantRules)
			}
		})
	}
}

// withName fills the computed id of a model, as a saved table has it.
func withName(model provider.TableModel) provider.TableModel {
	model.ID = types.StringValue("sales.orders")
	model.UpdatedAt = types.StringValue("t")
	model.UpdatedBy = types.StringValue("u")

	return model
}

func TestTableDelete(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name        string
		steps       []step
		wantSummary string
	}{
		{
			name:  "removes the table",
			steps: []step{{method: "DELETE", uri: "/tables?name=eq.sales.orders", profile: "console", response: "[]"}},
		},
		{
			name:        "reports a refused delete",
			steps:       []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantSummary: "Couldn't delete the table",
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			res := newTestTable(t, testCase.steps)
			s := resourceSchema(t, res)
			state := makeState(t, s, withName(tableConfig(t)))
			resp := resource.DeleteResponse{State: state}

			res.Delete(context.Background(), resource.DeleteRequest{State: state}, &resp)

			got := summaries(collectDiagnostics(resp.Diagnostics))

			switch {
			case testCase.wantSummary != "" && (len(got) != 1 || got[0] != testCase.wantSummary):
				t.Fatalf("diagnostics = %v, want %q", got, testCase.wantSummary)
			case testCase.wantSummary == "" && len(got) != 0:
				t.Fatalf("diagnostics = %v, want none", got)
			}
		})
	}
}

func TestTableValidateConfig(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		strategy types.String
		n        types.Int64
		fallback types.Bool
		want     []string
	}{
		{
			name:     "a partitioned table with n and fallback",
			strategy: types.StringValue("partitioned"),
			n:        types.Int64Value(3),
			fallback: types.BoolValue(true),
		},
		{
			name:     "a full table without n and fallback",
			strategy: types.StringValue("full"),
			n:        types.Int64Null(),
			fallback: types.BoolValue(false),
		},
		{
			name:     "a table without a strategy and with n",
			strategy: types.StringNull(),
			n:        types.Int64Value(3),
			fallback: types.BoolValue(false),
			want:     []string{"Invalid n"},
		},
		{
			name:     "a full table with n and fallback",
			strategy: types.StringValue("full"),
			n:        types.Int64Value(3),
			fallback: types.BoolValue(true),
			want:     []string{"Invalid n", "Invalid fallback"},
		},
		{
			name:     "an unknown strategy",
			strategy: types.StringUnknown(),
			n:        types.Int64Value(3),
			fallback: types.BoolValue(true),
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			res := newTestTable(t, nil)
			s := resourceSchema(t, res)
			model := tableConfig(t)
			model.Strategy, model.N, model.Fallback = testCase.strategy, testCase.n, testCase.fallback
			config := tfsdk.Config{Schema: s, Raw: makePlan(t, s, model).Raw}
			resp := resource.ValidateConfigResponse{}

			as[resource.ResourceWithValidateConfig](t, res).ValidateConfig(
				context.Background(),
				resource.ValidateConfigRequest{Config: config},
				&resp,
			)

			got := summaries(collectDiagnostics(resp.Diagnostics))
			if strings.Join(got, ",") != strings.Join(testCase.want, ",") {
				t.Errorf("diagnostics = %v, want %v", got, testCase.want)
			}
		})
	}
}

// TestTableImportReadsByName checks that the import id becomes the name of the table.
func TestTableImportReadsByName(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		id   string
	}{
		{name: "a qualified table", id: "sales.orders"},
		{name: "a table in a project with a dash", id: "my-project.dataset.table"},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			res := newTestTable(t, nil)
			s := resourceSchema(t, res)
			resp := resource.ImportStateResponse{State: nullState(s)}

			as[resource.ResourceWithImportState](t, res).ImportState(
				context.Background(),
				resource.ImportStateRequest{ID: testCase.id},
				&resp,
			)

			var state provider.TableModel

			resp.State.Get(context.Background(), &state)

			if state.Name.ValueString() != testCase.id {
				t.Errorf("name = %q, want %q", state.Name.ValueString(), testCase.id)
			}
		})
	}
}

func TestSameRules(t *testing.T) {
	t.Parallel()

	first := []client.RLSRule{rule("a", "x"), rule("b", "y")}
	tests := []struct {
		name  string
		right []client.RLSRule
		want  bool
	}{
		{name: "the same rules", right: []client.RLSRule{rule("a", "x"), rule("b", "y")}, want: true},
		{name: "the same rules in another order", right: []client.RLSRule{rule("b", "y"), rule("a", "x")}},
		{name: "fewer rules", right: first[:1]},
		{name: "no rules", right: nil},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			if got := provider.SameRules(first, testCase.right); got != testCase.want {
				t.Errorf("sameRules = %t, want %t", got, testCase.want)
			}
		})
	}
}

func TestRLSProblem(t *testing.T) {
	t.Parallel()

	text := types.StringValue("a")
	two := []provider.ConditionModel{{ColumnName: text, UnitType: text}, {ColumnName: text, UnitType: text}}

	tests := []struct {
		name  string
		block provider.RlsModel
		want  bool
	}{
		{name: "a flat block", block: provider.RlsModel{ColumnName: text, UnitType: text}},
		{
			name:  "two condition blocks",
			block: provider.RlsModel{ColumnName: types.StringNull(), UnitType: types.StringNull(), Conditions: two},
		},
		{
			name:  "a flat block with unknown values",
			block: provider.RlsModel{ColumnName: types.StringUnknown(), UnitType: types.StringUnknown()},
		},
		{name: "both forms", block: provider.RlsModel{ColumnName: text, UnitType: text, Conditions: two}, want: true},
		{
			name:  "a column and conditions",
			block: provider.RlsModel{ColumnName: text, UnitType: types.StringNull(), Conditions: two},
			want:  true,
		},
		{
			name:  "neither form",
			block: provider.RlsModel{ColumnName: types.StringNull(), UnitType: types.StringNull()},
			want:  true,
		},
		{
			name:  "a flat block with no unit type",
			block: provider.RlsModel{ColumnName: text, UnitType: types.StringNull()},
			want:  true,
		},
		{
			name:  "a flat block with no column",
			block: provider.RlsModel{ColumnName: types.StringNull(), UnitType: text},
			want:  true,
		},
		{
			name:  "one condition block",
			block: provider.RlsModel{ColumnName: types.StringNull(), UnitType: types.StringNull(), Conditions: two[:1]},
			want:  true,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()

			if got := provider.RlsProblem(test.block) != ""; got != test.want {
				t.Errorf("rlsProblem() refuses = %t, want %t", got, test.want)
			}
		})
	}
}

func TestRLSBlockRoundTripsBothForms(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		rule client.RLSRule
	}{
		{name: "one condition", rule: rule("area", "area")},
		{name: "two conditions", rule: rule("region_id", "region", "unit_id", "unit")},
		{name: "three conditions", rule: rule("a", "x", "b", "y", "c", "z")},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()

			block := provider.EncodeRule(test.rule)

			if problem := provider.RlsProblem(block); problem != "" {
				t.Fatalf("encodeRule() gives an invalid block: %s", problem)
			}

			if got := provider.DecodeRule(block); !reflect.DeepEqual(got, test.rule) {
				t.Errorf("decodeRule(provider.EncodeRule(%+v)) = %+v", test.rule, got)
			}
		})
	}
}

// tableAccConfig returns a schema with a claim and a table in it, with the row policies that the caller gives.
func tableAccConfig(project, table string, cacheTTL int, rules ...[2]string) string {
	blocks := ""

	var blocksSb543 strings.Builder
	for _, rule := range rules {
		fmt.Fprintf(&blocksSb543, `
  rls {
    column_name = %q
    unit_type   = %q
  }
`, rule[0], rule[1])
	}

	blocks += blocksSb543.String()

	return schemaConfig(project, "orders", "preferred_username") + fmt.Sprintf(`
resource "data_proxy_table" "this" {
  provider  = data-proxy
  name      = %q
  schema    = data_proxy_schema.this.name
  cache_ttl = %d
  sort      = ["order_id"]
%s}
`, table, cacheTTL, blocks)
}

//nolint:paralleltest // Acceptance tests share one OpenTofu run.
func TestAccTableResource(t *testing.T) {
	project := testAccName()
	table := "p.d." + testAccName()

	testAccCleanup(t, "projects", client.Eq("name", project))
	testAccCleanup(t, "schemas", client.Eq("project", project))
	testAccCleanup(t, "tables", client.Eq("name", table))

	unit, region := [2]string{"unit_id", "unit"}, [2]string{"region_id", "region"}

	acc.Test(t, acc.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories(),
		Steps: []acc.TestStep{
			{
				Config: tableAccConfig(project, table, 60, unit, region),
				Check: acc.ComposeAggregateTestCheckFunc(
					acc.TestCheckResourceAttr("data_proxy_table.this", "strategy", "full"),
					acc.TestCheckResourceAttr("data_proxy_table.this", "cache_ttl", "60"),
					acc.TestCheckResourceAttr("data_proxy_table.this", "rls.#", "2"),
					acc.TestCheckResourceAttr("data_proxy_table.this", "rls.0.column_name", "unit_id"),
					acc.TestCheckResourceAttr("data_proxy_table.this", "rls.1.unit_type", "region"),
				),
			},
			{
				Config: tableAccConfig(project, table, 120, region, unit),
				Check: acc.ComposeAggregateTestCheckFunc(
					acc.TestCheckResourceAttr("data_proxy_table.this", "cache_ttl", "120"),
					acc.TestCheckResourceAttr("data_proxy_table.this", "rls.0.column_name", "region_id"),
					acc.TestCheckResourceAttr("data_proxy_table.this", "rls.1.column_name", "unit_id"),
				),
			},
			{ResourceName: "data_proxy_table.this", ImportState: true, ImportStateVerify: true},
			{
				Config: tableAccConfig(project, table, 120),
				Check:  acc.TestCheckResourceAttr("data_proxy_table.this", "rls.#", "0"),
			},
		},
	})
}

// TestAccRLSHelpersAreNotServed checks over HTTP that a token reaches the rules only through rules() and replace_rls().
// The helpers write_rls and groups live in a schema that PostgREST does not serve, so each one is a 404.
//
//nolint:paralleltest // Acceptance tests share one OpenTofu run.
func TestAccRLSHelpersAreNotServed(t *testing.T) {
	if os.Getenv(acc.EnvTfAcc) == "" {
		t.Skipf("set %s to run the acceptance tests", acc.EnvTfAcc)
	}

	testAccPreCheck(t)

	project := testAccName()
	table := "p.d." + testAccName()

	testAccCleanup(t, "projects", client.Eq("name", project))
	testAccCleanup(t, "schemas", client.Eq("project", project))
	testAccCleanup(t, "tables", client.Eq("name", table))

	apiClient, err := testAccClient()
	if err != nil {
		t.Fatalf("testAccClient: %v", err)
	}

	ctx := context.Background()

	err = apiClient.Insert(ctx, client.ConsoleProfile, "projects", map[string]any{"name": project}, nil)
	if err != nil {
		t.Fatalf("add the project: %v", err)
	}

	schemaRow := map[string]any{"project": project, "short_name": "orders", "claim": "preferred_username"}

	err = apiClient.Insert(ctx, client.ConsoleProfile, "schemas", schemaRow, nil)
	if err != nil {
		t.Fatalf("add the schema: %v", err)
	}

	tableRow := map[string]any{"name": table, "schema": project + "__orders"}

	err = apiClient.Insert(ctx, client.ConsoleProfile, "tables", tableRow, nil)
	if err != nil {
		t.Fatalf("add the table: %v", err)
	}

	tests := []struct {
		name     string
		function string
		args     map[string]any
		wantErr  error
	}{
		{
			name:     "write_rls is not served",
			function: "write_rls",
			args:     map[string]any{"table_name": table, "groups": []any{}},
			wantErr:  client.ErrNotFound,
		},
		{name: "groups is not served", function: "groups", wantErr: client.ErrNotFound},
		{name: "rules is served", function: "rules"},
		{
			name:     "replace_rls is served",
			function: "replace_rls",
			args:     map[string]any{"table_name": table, "groups": []any{}},
		},
	}

	for _, testCase := range tests { //nolint:paralleltest // Acceptance tests share one OpenTofu run.
		t.Run(testCase.name, func(t *testing.T) {
			err := apiClient.RPC(ctx, client.ConsoleProfile, testCase.function, testCase.args, nil)

			if testCase.wantErr == nil && err != nil {
				t.Fatalf("RPC(%s) = %v, want no error", testCase.function, err)
			}

			if testCase.wantErr != nil && !errors.Is(err, testCase.wantErr) {
				t.Fatalf("RPC(%s) = %v, want %v", testCase.function, err, testCase.wantErr)
			}
		})
	}
}

//nolint:paralleltest // Acceptance tests share one OpenTofu run.
func TestAccTableResourceKeepsAGroupOfConditionsInOneBlock(t *testing.T) {
	project := testAccName()
	table := "p.d." + testAccName()

	testAccCleanup(t, "projects", client.Eq("name", project))
	testAccCleanup(t, "schemas", client.Eq("project", project))
	testAccCleanup(t, "tables", client.Eq("name", table))

	config := schemaConfig(project, "orders", "preferred_username") + fmt.Sprintf(`
resource "data_proxy_table" "this" {
  provider = data-proxy
  name     = %q
  schema   = data_proxy_schema.this.name

  rls {
    condition {
      column_name = "region_id"
      unit_type   = "region"
    }
    condition {
      column_name = "unit_id"
      unit_type   = "unit"
    }
  }

  rls {
    column_name = "area"
    unit_type   = "area"
  }
}
`, table)

	acc.Test(t, acc.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories(),
		Steps: []acc.TestStep{
			{
				Config: config,
				Check: acc.ComposeAggregateTestCheckFunc(
					acc.TestCheckResourceAttr("data_proxy_table.this", "rls.#", "2"),
					acc.TestCheckResourceAttr("data_proxy_table.this", "rls.0.condition.#", "2"),
					acc.TestCheckResourceAttr("data_proxy_table.this", "rls.0.condition.1.column_name", "unit_id"),
					acc.TestCheckResourceAttr("data_proxy_table.this", "rls.1.column_name", "area"),
				),
			},
			{ResourceName: "data_proxy_table.this", ImportState: true, ImportStateVerify: true},
		},
	})
}

//nolint:paralleltest // Acceptance tests share one OpenTofu run.
func TestAccTableResourceRefusesARowPolicyWithoutAClaim(t *testing.T) {
	project := testAccName()
	table := "p.d." + testAccName()

	testAccCleanup(t, "projects", client.Eq("name", project))
	testAccCleanup(t, "schemas", client.Eq("project", project))
	testAccCleanup(t, "tables", client.Eq("name", table))

	config := projectConfig(project) + fmt.Sprintf(`
resource "data_proxy_schema" "this" {
  provider            = data-proxy
  project             = data_proxy_project.this.name
  short_name          = "orders"
  deletion_protection = false

  timeouts {
    create = "0s"
    delete = "0s"
  }
}

resource "data_proxy_table" "this" {
  provider = data-proxy
  name     = %q
  schema   = data_proxy_schema.this.name

  rls {
    column_name = "unit_id"
    unit_type   = "unit"
  }
}
`, table)

	acc.Test(t, acc.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories(),
		Steps: []acc.TestStep{
			{Config: config, ExpectError: regexp.MustCompile(`Couldn't add the row policies of the table`)},
		},
	})
}

//nolint:paralleltest // Acceptance tests share one OpenTofu run.
func TestAccTableResourceRefusesNWithAFullStrategy(t *testing.T) {
	acc.Test(t, acc.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories(),
		Steps: []acc.TestStep{
			{
				Config: `
resource "data_proxy_table" "this" {
  provider = data-proxy
  name     = "p.d.t"
  schema   = "s"
  n        = 3
}
`,
				ExpectError: regexp.MustCompile(`n needs the strategy`),
			},
		},
	})
}

//nolint:paralleltest // Acceptance tests share one OpenTofu run.
func TestAccTableResourceRefusesAnRLSBlockThatMixesBothForms(t *testing.T) {
	tests := []struct {
		name  string
		block string
		want  string
	}{
		{
			name: "both forms",
			block: `
  rls {
    column_name = "a"
    unit_type   = "x"
    condition {
      column_name = "b"
      unit_type   = "y"
    }
    condition {
      column_name = "c"
      unit_type   = "z"
    }
  }`,
			want: `both a column and conditions`,
		},
		{
			name: "one condition block",
			block: `
  rls {
    condition {
      column_name = "a"
      unit_type   = "x"
    }
  }`,
			want: `written flat`,
		},
		{name: "an empty block", block: `
  rls {}`, want: `needs column_name and unit_type`},
	}

	for _, test := range tests { //nolint:paralleltest // Acceptance tests share one OpenTofu run.
		t.Run(test.name, func(t *testing.T) {
			acc.Test(t, acc.TestCase{
				PreCheck:                 func() { testAccPreCheck(t) },
				ProtoV6ProviderFactories: testAccProviderFactories(),
				Steps: []acc.TestStep{
					{
						Config: `
resource "data_proxy_table" "this" {
  provider = data-proxy
  name     = "p.d.t"
  schema   = "s"
` + test.block + `
}
`,
						ExpectError: regexp.MustCompile(test.want),
					},
				},
			})
		})
	}
}
