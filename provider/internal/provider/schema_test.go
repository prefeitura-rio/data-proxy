package provider_test

import (
	"context"
	"data-proxy/internal/client"
	"data-proxy/internal/provider"
	"fmt"
	"net/http"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"
	acc "github.com/hashicorp/terraform-plugin-testing/helper/resource"
)

// schemaRowJSON is the row that the fake API answers for the schema orders of the project sales.
const schemaRowJSON = `{"name":"sales_orders","project":"sales","short_name":"orders","source":"bigquery",` +
	`"settings":{"b":1,"a":2},` +
	`"claim":"preferred_username","encrypted":false,"updated_at":"2026-01-01T00:00:00Z","updated_by":"terraform"}`

// healthReadyStep() is the answer of the health view for a schema that exists.
func healthReadyStep() step {
	return step{
		method:   http.MethodGet,
		uri:      "/health?name=eq.sales_orders",
		response: `[{"name":"sales_orders","schema_exists":true,"view_count":0}]`,
	}
}

// probeReadyStep() is the answer of PostgREST for a schema that it serves.
func probeReadyStep() step {
	return step{method: http.MethodGet, uri: "/", profile: "sales_orders", response: "{}"}
}

// probeGoneStep() is the answer of PostgREST for a schema that it no longer serves.
func probeGoneStep() step {
	return step{
		method:   http.MethodGet,
		uri:      "/",
		profile:  "sales_orders",
		status:   http.StatusNotAcceptable,
		response: `{"code":"PGRST106","message":"The schema must be one of the following"}`,
	}
}

// newTestSchema makes the schema resource with no retry delay, and with a client that follows the steps.
func newTestSchema(t *testing.T, steps []step) resource.Resource {
	t.Helper()

	testSchema := provider.SchemaResourceWithNoRetry()

	return configured(t, testSchema, newStepClient(t, steps))
}

// schemaPlan returns the planned model of a new schema. The create timeout is the text that the test passes.
func schemaPlan(createTimeout string) provider.SchemaModel {
	return provider.SchemaModel{
		ID:                 types.StringUnknown(),
		Project:            types.StringValue("sales"),
		ShortName:          types.StringValue("orders"),
		Name:               types.StringUnknown(),
		Source:             types.StringValue("bigquery"),
		Settings:           types.StringValue(`{"a": 2, "b": 1}`),
		Claim:              types.StringValue("preferred_username"),
		Encrypted:          types.BoolValue(false),
		DeletionProtection: types.BoolValue(true),
		UpdatedAt:          types.StringUnknown(),
		UpdatedBy:          types.StringUnknown(),
		Timeouts:           makeTimeouts(createTimeout, ""),
	}
}

// schemaState returns the model of a schema that exists. The arguments set the deletion protection and the delete
// timeout.
func schemaState(protected bool, deleteTimeout string) provider.SchemaModel {
	model := schemaPlan("")
	provider.ApplySchemaRow(&model, client.Schema{
		Name: "sales_orders", Project: "sales", ShortName: "orders", Source: "bigquery",
		Settings: []byte(`{"a":2,"b":1}`), Claim: new("preferred_username"), UpdatedAt: "old", UpdatedBy: "old",
	})
	model.Settings = types.StringValue(`{"a": 2, "b": 1}`)
	model.DeletionProtection = types.BoolValue(protected)
	model.Timeouts = makeTimeouts("", deleteTimeout)

	return model
}

func TestSchemaCreate(t *testing.T) {
	t.Parallel()

	create := step{
		method:   "POST",
		uri:      "/schemas",
		profile:  "console",
		status:   201,
		response: "[" + schemaRowJSON + "]",
		body: `{"project":"sales","short_name":"orders","source":"bigquery","settings":{"a":2,"b":1},` +
			`"claim":"preferred_username","encrypted":false}`,
	}

	tests := []struct {
		name          string
		createTimeout string
		steps         []step
		wantSummary   string
	}{
		{
			name:  "adds the schema and waits until it is provisioned",
			steps: []step{create, healthReadyStep(), probeReadyStep()},
		},
		{name: "skips the wait with a timeout of zero", createTimeout: "0s", steps: []step{create}},
		{
			name:          "keeps the schema when the wait fails",
			createTimeout: "1ns",
			steps:         []step{create},
			wantSummary:   "The schema is not ready",
		},
		{
			name:        "reports a refused write",
			steps:       []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantSummary: "Couldn't create the schema",
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			testSchema := newTestSchema(t, testCase.steps)
			s := resourceSchema(t, testSchema)
			resp := resource.CreateResponse{State: nullState(s)}

			testSchema.Create(
				context.Background(),
				resource.CreateRequest{Plan: makePlan(t, s, schemaPlan(testCase.createTimeout))},
				&resp,
			)

			got := summaries(collectDiagnostics(resp.Diagnostics))

			if testCase.wantSummary != "" {
				if len(got) != 1 || got[0] != testCase.wantSummary {
					t.Fatalf("diagnostics = %v, want %q", got, testCase.wantSummary)
				}
			} else if len(got) != 0 {
				t.Fatalf("diagnostics = %v, want none", got)
			}

			if testCase.wantSummary == "Couldn't create the schema" {
				if !resp.State.Raw.IsNull() {
					t.Errorf("state = %v, want none after a refused write", resp.State.Raw)
				}

				return
			}

			var state provider.SchemaModel

			resp.State.Get(context.Background(), &state)

			if state.Name.ValueString() != "sales_orders" || state.ID.ValueString() != "sales_orders" {
				t.Errorf("name = %q, id = %q, want the physical name", state.Name.ValueString(), state.ID.ValueString())
			}

			if state.Settings.ValueString() != `{"a": 2, "b": 1}` {
				t.Errorf("settings = %q, want the layout of the configuration", state.Settings.ValueString())
			}
		})
	}
}

func TestSchemaRead(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name        string
		steps       []step
		wantRemoved bool
		wantSummary string
	}{
		{
			name:  "refreshes the row",
			steps: []step{{method: "GET", uri: "/schemas?name=eq.sales_orders", response: "[" + schemaRowJSON + "]"}},
		},
		{name: "drops a schema that no longer exists", steps: []step{{response: "[]"}}, wantRemoved: true},
		{
			name:        "reports a read that fails",
			steps:       []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantSummary: "Couldn't read the schema",
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			testSchema := newTestSchema(t, testCase.steps)
			s := resourceSchema(t, testSchema)
			state := makeState(t, s, schemaState(true, ""))
			resp := resource.ReadResponse{State: state}

			testSchema.Read(context.Background(), resource.ReadRequest{State: state}, &resp)

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
				var read provider.SchemaModel

				resp.State.Get(context.Background(), &read)

				if read.UpdatedAt.ValueString() != "2026-01-01T00:00:00Z" || read.Settings.ValueString() != `{"a": 2, "b": 1}` {
					t.Errorf("state = %+v, want the refreshed row with the layout of the provider.Settings kept", read)
				}
			}
		})
	}
}

// TestSchemaUpdate checks an update that the server accepts, and one that it refuses.
func TestSchemaUpdate(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name        string
		step        step
		wantSummary string
		wantDetail  string
	}{
		{
			name: "the server accepts the write",
			step: step{
				method: "PATCH", uri: "/schemas?name=eq.sales_orders", profile: "console", response: "[" + schemaRowJSON + "]",
				body: `{"source":"bigquery","settings":{"a":2,"b":1},"claim":"preferred_username","encrypted":false}`,
			},
		},
		{
			name: "the server refuses the write",
			step: step{
				status:   400,
				response: `{"code":"23514","message":"The name of schema sales_orders can't change"}`,
			},
			wantSummary: "Couldn't update the schema",
			wantDetail:  "can't change",
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			res := newTestSchema(t, []step{testCase.step})
			s := resourceSchema(t, res)
			plan := schemaState(true, "")
			resp := resource.UpdateResponse{State: makeState(t, s, plan)}

			res.Update(context.Background(), resource.UpdateRequest{Plan: makePlan(t, s, plan), State: makeState(t, s, plan)}, &resp)

			got := collectDiagnostics(resp.Diagnostics)

			if testCase.wantSummary == "" {
				if len(got) != 0 {
					t.Fatalf("diagnostics = %+v, want none", got)
				}

				return
			}

			if len(got) != 1 || got[0].summary != testCase.wantSummary || !strings.Contains(got[0].detail, testCase.wantDetail) {
				t.Fatalf("diagnostics = %+v, want %q with %q", got, testCase.wantSummary, testCase.wantDetail)
			}
		})
	}
}

func TestSchemaDelete(t *testing.T) {
	t.Parallel()

	deleteStep := step{method: "DELETE", uri: "/schemas?name=eq.sales_orders", profile: "console", response: "[]"}

	tests := []struct {
		name          string
		protected     bool
		deleteTimeout string
		steps         []step
		wantSummary   string
	}{
		{
			name:        "refuses while the schema has deletion protection",
			protected:   true,
			wantSummary: "The schema has deletion protection",
		},
		{name: "removes the schema and waits until the sync drops it", steps: []step{deleteStep, probeGoneStep()}},
		{name: "skips the wait with a timeout of zero", deleteTimeout: "0s", steps: []step{deleteStep}},
		{
			name:        "reports a refused delete",
			steps:       []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantSummary: "Couldn't delete the schema",
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			testSchema := newTestSchema(t, testCase.steps)
			s := resourceSchema(t, testSchema)
			state := makeState(t, s, schemaState(testCase.protected, testCase.deleteTimeout))
			resp := resource.DeleteResponse{State: state}

			testSchema.Delete(context.Background(), resource.DeleteRequest{State: state}, &resp)

			got := summaries(collectDiagnostics(resp.Diagnostics))

			switch {
			case testCase.wantSummary == "" && len(got) != 0:
				t.Fatalf("diagnostics = %v, want none", got)
			case testCase.wantSummary != "" && (len(got) != 1 || got[0] != testCase.wantSummary):
				t.Fatalf("diagnostics = %v, want %q", got, testCase.wantSummary)
			}
		})
	}
}

// TestSchemaImportKeepsDeletionProtection checks that an imported schema has its name and deletion protection.
func TestSchemaImportKeepsDeletionProtection(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		id   string
	}{
		{name: "a physical name", id: "sales_orders"},
		{name: "a name with a project prefix", id: "proj_orders"},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			res := newTestSchema(t, nil)
			s := resourceSchema(t, res)
			resp := resource.ImportStateResponse{State: nullState(s)}

			as[resource.ResourceWithImportState](t, res).ImportState(
				context.Background(),
				resource.ImportStateRequest{ID: testCase.id},
				&resp,
			)

			var state provider.SchemaModel

			resp.State.Get(context.Background(), &state)

			if state.Name.ValueString() != testCase.id || !state.DeletionProtection.ValueBool() {
				t.Errorf("state = %+v, want the name %q and deletion protection", state, testCase.id)
			}
		})
	}
}

//nolint:paralleltest // Acceptance tests share one OpenTofu run.
func TestAccSchemaResource(t *testing.T) {
	project := testAccName()
	testAccCleanup(t, "projects", client.Eq("name", project))
	testAccCleanup(t, "schemas", client.Eq("project", project))

	physical := project + "__orders"

	acc.Test(t, acc.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories(),
		Steps: []acc.TestStep{
			{
				Config: schemaConfig(project, "orders", "preferred_username"),
				Check: acc.ComposeAggregateTestCheckFunc(
					acc.TestCheckResourceAttr("data_proxy_schema.this", "name", physical),
					acc.TestCheckResourceAttr("data_proxy_schema.this", "id", physical),
					acc.TestCheckResourceAttr("data_proxy_schema.this", "source", "bigquery"),
					acc.TestCheckResourceAttr("data_proxy_schema.this", "claim", "preferred_username"),
				),
			},
			{
				Config: schemaConfig(project, "orders", "email"),
				Check:  acc.TestCheckResourceAttr("data_proxy_schema.this", "claim", "email"),
			},
			{
				ResourceName:            "data_proxy_schema.this",
				ImportState:             true,
				ImportStateVerify:       true,
				ImportStateVerifyIgnore: []string{"deletion_protection", "timeouts"},
			},
			{
				Config: schemaConfig(project, "invoices", "email"),
				Check:  acc.TestCheckResourceAttr("data_proxy_schema.this", "name", project+"__invoices"),
			},
		},
	})
}

//nolint:paralleltest // Acceptance tests share one OpenTofu run.
func TestAccSchemaResourceInTheDefaultProject(t *testing.T) {
	short := testAccName()
	testAccCleanup(t, "schemas", client.Eq("name", "default__"+short))

	acc.Test(t, acc.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories(),
		Steps: []acc.TestStep{
			{
				Config: fmt.Sprintf(`
resource "data_proxy_schema" "this" {
  provider            = data-proxy
  short_name          = %q
  deletion_protection = false

  timeouts {
    create = "0s"
    delete = "0s"
  }
}
`, short),
				Check: acc.ComposeAggregateTestCheckFunc(
					acc.TestCheckResourceAttr("data_proxy_schema.this", "project", "default"),
					acc.TestCheckResourceAttr("data_proxy_schema.this", "name", "default__"+short),
				),
			},
		},
	})
}

// TestSchemaUpgradeStateNamesTheSchemaByItsProject checks that the upgrade of the state from version 0 gives each
// schema
// the physical name with two underscores, also in the project default.
func TestSchemaUpgradeStateNamesTheSchemaByItsProject(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name    string
		project string
		short   string
		want    string
	}{
		{name: "a schema of another project", project: "sales", short: "orders", want: "sales__orders"},
		{name: "a schema of the project default", project: "default", short: "hr", want: "default__hr"},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			testSchema := as[*provider.SchemaResource](t, provider.NewSchemaResource())
			upgrader := testSchema.UpgradeState(context.Background())[0]
			old := schemaState(true, "")
			old.Project = types.StringValue(testCase.project)
			old.ShortName = types.StringValue(testCase.short)
			old.ID = types.StringValue(testCase.short)
			old.Name = types.StringValue(testCase.short)
			prior := makeState(t, *upgrader.PriorSchema, old)
			resp := resource.UpgradeStateResponse{State: nullState(resourceSchema(t, testSchema))}

			upgrader.StateUpgrader(context.Background(), resource.UpgradeStateRequest{State: &prior}, &resp)

			if resp.Diagnostics.HasError() {
				t.Fatalf("upgrade: %v", resp.Diagnostics)
			}

			var got provider.SchemaModel

			resp.State.Get(context.Background(), &got)

			if got.ID.ValueString() != testCase.want || got.Name.ValueString() != testCase.want {
				t.Errorf("id and name = %q and %q, want %q", got.ID.ValueString(), got.Name.ValueString(), testCase.want)
			}
		})
	}
}
