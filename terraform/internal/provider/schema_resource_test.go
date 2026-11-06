package provider

import (
	"context"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"terraform-provider-data-proxy/internal/client"
)

// schemaRowJSON is the row that the test server gives for the schema orders of the project sales.
const schemaRowJSON = `{"name":"sales_orders","project":"sales","short_name":"orders","source":"bigquery","settings":{"b":1,"a":2},` +
	`"claim":"preferred_username","encrypted":false,"updated_at":"2026-01-01T00:00:00Z","updated_by":"terraform"}`

// Steps that a provisioned schema answers: the health row and a read of its access policy table.
var (
	healthReadyStep = step{method: "GET", uri: "/health?name=eq.sales_orders", response: `[{"name":"sales_orders","schema_exists":true,"access_policy":true,"view_count":0}]`}
	probeReadyStep  = step{method: "GET", uri: "/access_policy?limit=0", profile: "sales_orders", response: "[]"}
	probeGoneStep   = step{method: "GET", uri: "/access_policy?limit=0", profile: "sales_orders", status: 406, response: `{"code":"PGRST106","message":"The schema must be one of the following"}`}
)

// newTestSchema returns the resource with a client that the steps drive, and no wait between reads.
func newTestSchema(t *testing.T, steps []step) resource.Resource {
	t.Helper()

	res := newSchemaResource()
	res.(*schemaResource).backoff = client.Backoff{}

	return configured(t, res, newStepClient(t, steps))
}

// schemaPlan returns the plan of a new schema with the given create timeout.
func schemaPlan(createTimeout string) schemaModel {
	return schemaModel{
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
		Timeouts:           timeoutsOf(createTimeout, ""),
	}
}

// schemaState returns the state of the saved schema.
func schemaState(protected bool, deleteTimeout string) schemaModel {
	model := schemaPlan("")
	applySchemaRow(&model, client.Schema{
		Name: "sales_orders", Project: "sales", ShortName: "orders", Source: "bigquery",
		Settings: []byte(`{"a":2,"b":1}`), Claim: ptr("preferred_username"), UpdatedAt: "old", UpdatedBy: "old",
	})
	model.Settings = types.StringValue(`{"a": 2, "b": 1}`)
	model.DeletionProtection = types.BoolValue(protected)
	model.Timeouts = timeoutsOf("", deleteTimeout)

	return model
}

func TestSchemaCreate(t *testing.T) {
	create := step{
		method: "POST", uri: "/schemas", profile: "console", status: 201, response: "[" + schemaRowJSON + "]",
		body: `{"project":"sales","short_name":"orders","source":"bigquery","settings":{"a":2,"b":1},"claim":"preferred_username","encrypted":false}`,
	}

	tests := []struct {
		name          string
		createTimeout string
		steps         []step
		wantSummary   string
	}{
		{name: "adds the schema and waits until it is provisioned", steps: []step{create, healthReadyStep, probeReadyStep}},
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

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			res := newTestSchema(t, tt.steps)
			s := resourceSchema(t, res)
			resp := resource.CreateResponse{State: nullState(s)}

			res.Create(context.Background(), resource.CreateRequest{Plan: planOf(t, s, schemaPlan(tt.createTimeout))}, &resp)

			got := summaries(diagnosticsOf(resp.Diagnostics))

			if tt.wantSummary != "" {
				if len(got) != 1 || got[0] != tt.wantSummary {
					t.Fatalf("diagnostics = %v, want %q", got, tt.wantSummary)
				}
			} else if len(got) != 0 {
				t.Fatalf("diagnostics = %v, want none", got)
			}

			if tt.wantSummary == "Couldn't create the schema" {
				if !resp.State.Raw.IsNull() {
					t.Errorf("state = %v, want none after a refused write", resp.State.Raw)
				}

				return
			}

			var state schemaModel

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
	tests := []struct {
		name        string
		steps       []step
		wantRemoved bool
		wantSummary string
	}{
		{name: "refreshes the row", steps: []step{{method: "GET", uri: "/schemas?name=eq.sales_orders", response: "[" + schemaRowJSON + "]"}}},
		{name: "drops a schema that no longer exists", steps: []step{{response: "[]"}}, wantRemoved: true},
		{name: "reports a read that fails", steps: []step{{status: 403, response: `{"code":"42501","message":"denied"}`}}, wantSummary: "Couldn't read the schema"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			res := newTestSchema(t, tt.steps)
			s := resourceSchema(t, res)
			state := stateOf(t, s, schemaState(true, ""))
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
				var read schemaModel

				resp.State.Get(context.Background(), &read)

				if read.UpdatedAt.ValueString() != "2026-01-01T00:00:00Z" || read.Settings.ValueString() != `{"a": 2, "b": 1}` {
					t.Errorf("state = %+v, want the refreshed row with the layout of the settings kept", read)
				}
			}
		})
	}
}

func TestSchemaUpdate(t *testing.T) {
	res := newTestSchema(t, []step{{
		method: "PATCH", uri: "/schemas?name=eq.sales_orders", profile: "console", response: "[" + schemaRowJSON + "]",
		body: `{"source":"bigquery","settings":{"a":2,"b":1},"claim":"preferred_username","encrypted":false}`,
	}})
	s := resourceSchema(t, res)
	plan := schemaState(true, "")
	resp := resource.UpdateResponse{State: stateOf(t, s, plan)}

	res.Update(context.Background(), resource.UpdateRequest{Plan: planOf(t, s, plan), State: stateOf(t, s, plan)}, &resp)

	if got := diagnosticsOf(resp.Diagnostics); len(got) != 0 {
		t.Fatalf("diagnostics = %+v, want none", got)
	}
}

func TestSchemaUpdateReportsARefusedWrite(t *testing.T) {
	res := newTestSchema(t, []step{{status: 400, response: `{"code":"23514","message":"The name of schema sales_orders can't change"}`}})
	s := resourceSchema(t, res)
	plan := schemaState(true, "")
	resp := resource.UpdateResponse{State: stateOf(t, s, plan)}

	res.Update(context.Background(), resource.UpdateRequest{Plan: planOf(t, s, plan), State: stateOf(t, s, plan)}, &resp)

	got := diagnosticsOf(resp.Diagnostics)
	if len(got) != 1 || got[0].summary != "Couldn't update the schema" || !strings.Contains(got[0].detail, "can't change") {
		t.Fatalf("diagnostics = %+v, want the refused update", got)
	}
}

func TestSchemaDelete(t *testing.T) {
	deleteStep := step{method: "DELETE", uri: "/schemas?name=eq.sales_orders", profile: "console", response: "[]"}

	tests := []struct {
		name          string
		protected     bool
		deleteTimeout string
		steps         []step
		wantSummary   string
	}{
		{name: "refuses while the schema has deletion protection", protected: true, wantSummary: "The schema has deletion protection"},
		{name: "removes the schema and waits until the sync drops it", steps: []step{deleteStep, probeGoneStep}},
		{name: "skips the wait with a timeout of zero", deleteTimeout: "0s", steps: []step{deleteStep}},
		{
			name:        "reports a refused delete",
			steps:       []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantSummary: "Couldn't delete the schema",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			res := newTestSchema(t, tt.steps)
			s := resourceSchema(t, res)
			state := stateOf(t, s, schemaState(tt.protected, tt.deleteTimeout))
			resp := resource.DeleteResponse{State: state}

			res.Delete(context.Background(), resource.DeleteRequest{State: state}, &resp)

			got := summaries(diagnosticsOf(resp.Diagnostics))

			switch {
			case tt.wantSummary == "" && len(got) != 0:
				t.Fatalf("diagnostics = %v, want none", got)
			case tt.wantSummary != "" && (len(got) != 1 || got[0] != tt.wantSummary):
				t.Fatalf("diagnostics = %v, want %q", got, tt.wantSummary)
			}
		})
	}
}

func TestSchemaImportKeepsDeletionProtection(t *testing.T) {
	res := newTestSchema(t, nil)
	s := resourceSchema(t, res)
	resp := resource.ImportStateResponse{State: nullState(s)}

	res.(resource.ResourceWithImportState).ImportState(context.Background(), resource.ImportStateRequest{ID: "sales_orders"}, &resp)

	var state schemaModel

	resp.State.Get(context.Background(), &state)

	if state.Name.ValueString() != "sales_orders" || !state.DeletionProtection.ValueBool() {
		t.Errorf("state = %+v, want the name and deletion protection", state)
	}
}
