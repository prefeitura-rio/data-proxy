package provider

import (
	"context"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework-timeouts/resource/timeouts"
	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"terraform-provider-data-proxy/internal/client"
)

// newTestAccessPolicy returns the resource with a client that the steps drive, and no wait between reads.
func newTestAccessPolicy(t *testing.T, steps []step) resource.Resource {
	t.Helper()

	res := newAccessPolicyResource()
	res.(*accessPolicyResource).backoff = client.Backoff{}

	return configured(t, res, newStepClient(t, steps))
}

// policyConfig returns the model of a policy of the schema sales_orders with the given units.
func policyConfig(unitType, unitID types.String, id types.String) accessPolicyModel {
	return accessPolicyModel{
		ID:       id,
		Schema:   types.StringValue("sales_orders"),
		Subject:  types.StringValue("reader@example.org"),
		UnitType: unitType,
		UnitID:   unitID,
		Timeouts: timeouts.Value{Object: types.ObjectNull(map[string]attr.Type{"create": types.StringType})},
	}
}

// The request and the row of the tests: a policy of the unit 42.
const (
	policyURI    = "/access_policy?subject=eq.reader%40example.org&unit_type=eq.unit&unit_id=eq.42"
	policyJSON   = `{"subject":"reader@example.org","unit_type":"unit","unit_id":"42"}`
	policyUnit   = "unit"
	policyUnitID = "42"
)

func TestAccessPolicyCreate(t *testing.T) {
	tests := []struct {
		name        string
		steps       []step
		unitType    types.String
		unitID      types.String
		wantID      string
		wantSummary string
		wantDetail  string
	}{
		{
			name:     "adds a row for a unit",
			steps:    []step{{method: "POST", uri: "/access_policy", profile: "sales_orders", status: 201, body: policyJSON}},
			unitType: types.StringValue(policyUnit), unitID: types.StringValue(policyUnitID),
			wantID: "sales_orders/reader@example.org/unit/42",
		},
		{
			name:     "adds a row for the whole schema with NULL units",
			steps:    []step{{method: "POST", uri: "/access_policy", status: 201, body: `{"subject":"reader@example.org","unit_type":null,"unit_id":null}`}},
			unitType: types.StringNull(), unitID: types.StringNull(),
			wantID: "sales_orders/reader@example.org//",
		},
		{
			name:        "names the permission of a refused write",
			steps:       []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			unitType:    types.StringNull(),
			unitID:      types.StringNull(),
			wantSummary: "Couldn't create the access policy",
			wantDetail:  "create_policy",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			res := newTestAccessPolicy(t, tt.steps)
			s := resourceSchema(t, res)
			plan := planOf(t, s, policyConfig(tt.unitType, tt.unitID, types.StringUnknown()))
			resp := resource.CreateResponse{State: nullState(s)}

			res.Create(context.Background(), resource.CreateRequest{Plan: plan}, &resp)

			got := diagnosticsOf(resp.Diagnostics)

			if tt.wantSummary != "" {
				if len(got) != 1 || got[0].summary != tt.wantSummary || !strings.Contains(got[0].detail, tt.wantDetail) {
					t.Fatalf("diagnostics = %+v, want %q with %q", got, tt.wantSummary, tt.wantDetail)
				}

				return
			}

			if len(got) != 0 {
				t.Fatalf("diagnostics = %+v, want none", got)
			}

			var state accessPolicyModel

			resp.State.Get(context.Background(), &state)

			if state.ID.ValueString() != tt.wantID {
				t.Errorf("id = %q, want %q", state.ID.ValueString(), tt.wantID)
			}
		})
	}
}

func TestAccessPolicyRead(t *testing.T) {
	tests := []struct {
		name        string
		steps       []step
		wantRemoved bool
		wantSummary string
		wantDetail  string
	}{
		{name: "finds the one row", steps: []step{{method: "GET", uri: policyURI, profile: "sales_orders", response: "[" + policyJSON + "]"}}},
		{name: "drops a row that no longer exists", steps: []step{{response: "[]"}}, wantRemoved: true},
		{
			name:        "refuses duplicate rows",
			steps:       []step{{response: "[" + policyJSON + "," + policyJSON + "]"}},
			wantSummary: "The access policy has duplicate rows",
			wantDetail:  "no primary key",
		},
		{
			name:        "reports a read that fails",
			steps:       []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantSummary: "Couldn't read the access policy",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			res := newTestAccessPolicy(t, tt.steps)
			s := resourceSchema(t, res)
			state := stateOf(t, s, policyConfig(types.StringValue(policyUnit), types.StringValue(policyUnitID), types.StringValue("sales_orders/reader@example.org/unit/42")))
			resp := resource.ReadResponse{State: state}

			res.Read(context.Background(), resource.ReadRequest{State: state}, &resp)

			got := diagnosticsOf(resp.Diagnostics)

			switch {
			case tt.wantSummary != "":
				if len(got) != 1 || got[0].summary != tt.wantSummary || !strings.Contains(got[0].detail, tt.wantDetail) {
					t.Fatalf("diagnostics = %+v, want %q with %q", got, tt.wantSummary, tt.wantDetail)
				}
			case tt.wantRemoved:
				if !resp.State.Raw.IsNull() {
					t.Errorf("state = %v, want it removed", resp.State.Raw)
				}
			case len(got) != 0:
				t.Fatalf("diagnostics = %+v, want none", got)
			}
		})
	}
}

func TestAccessPolicyDelete(t *testing.T) {
	tests := []struct {
		name        string
		steps       []step
		wantSummary string
	}{
		{name: "removes the row", steps: []step{{method: "DELETE", uri: policyURI, profile: "sales_orders", response: "[]"}}},
		{name: "reports a refused delete", steps: []step{{status: 403, response: `{"code":"42501","message":"denied"}`}}, wantSummary: "Couldn't delete the access policy"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			res := newTestAccessPolicy(t, tt.steps)
			s := resourceSchema(t, res)
			state := stateOf(t, s, policyConfig(types.StringValue(policyUnit), types.StringValue(policyUnitID), types.StringValue("x")))
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

func TestParseAccessPolicyID(t *testing.T) {
	tests := []struct {
		name       string
		id         string
		wantSchema string
		want       client.AccessPolicy
		wantErr    bool
	}{
		{name: "a unit", id: "s/me@example.org/unit/42", wantSchema: "s", want: client.AccessPolicy{Subject: "me@example.org", UnitType: ptr("unit"), UnitID: ptr("42")}},
		{name: "no unit", id: "s/svc//", wantSchema: "s", want: client.AccessPolicy{Subject: "svc"}},
		{name: "a subject with slashes", id: "s/a/b/c/unit/7", wantSchema: "s", want: client.AccessPolicy{Subject: "a/b/c", UnitType: ptr("unit"), UnitID: ptr("7")}},
		{name: "too few parts", id: "s/me", wantErr: true},
		{name: "no schema", id: "/me//", wantErr: true},
		{name: "no subject", id: "s///", wantErr: true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			schemaName, policy, err := parseAccessPolicyID(tt.id)

			if tt.wantErr {
				if err == nil {
					t.Fatalf("parseAccessPolicyID(%q) = %q, %+v, want an error", tt.id, schemaName, policy)
				}

				return
			}

			if err != nil || schemaName != tt.wantSchema || policy.Subject != tt.want.Subject ||
				derefString(policy.UnitType) != derefString(tt.want.UnitType) || derefString(policy.UnitID) != derefString(tt.want.UnitID) ||
				(policy.UnitType == nil) != (tt.want.UnitType == nil) {
				t.Errorf("parseAccessPolicyID(%q) = %q, %+v, %v, want %q, %+v", tt.id, schemaName, policy, err, tt.wantSchema, tt.want)
			}
		})
	}
}

func TestAccessPolicyImport(t *testing.T) {
	res := newTestAccessPolicy(t, nil)
	s := resourceSchema(t, res)
	importer := res.(resource.ResourceWithImportState)

	t.Run("sets the parts of the ID", func(t *testing.T) {
		resp := resource.ImportStateResponse{State: nullState(s)}

		importer.ImportState(context.Background(), resource.ImportStateRequest{ID: "s/me@example.org/unit/42"}, &resp)

		var state accessPolicyModel

		resp.State.Get(context.Background(), &state)

		if state.Schema.ValueString() != "s" || state.Subject.ValueString() != "me@example.org" || state.UnitID.ValueString() != "42" {
			t.Errorf("state = %+v, want the parts of the ID", state)
		}
	})

	t.Run("refuses an ID without the parts", func(t *testing.T) {
		resp := resource.ImportStateResponse{State: nullState(s)}

		importer.ImportState(context.Background(), resource.ImportStateRequest{ID: "nope"}, &resp)

		if got := summaries(diagnosticsOf(resp.Diagnostics)); len(got) != 1 || got[0] != "Invalid import ID" {
			t.Errorf("diagnostics = %v, want the error of the ID", got)
		}
	})
}

func TestAccessPolicyUpdateNeverRuns(t *testing.T) {
	res := newTestAccessPolicy(t, nil)
	resp := resource.UpdateResponse{}

	res.Update(context.Background(), resource.UpdateRequest{}, &resp)

	if got := summaries(diagnosticsOf(resp.Diagnostics)); len(got) != 1 || got[0] != "An access policy can't change" {
		t.Errorf("diagnostics = %v, want the error of an update", got)
	}
}
