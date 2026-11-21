package provider

import (
	"context"
	"fmt"
	"strings"
	"terraform-provider-data-proxy/internal/client"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"
	acc "github.com/hashicorp/terraform-plugin-testing/helper/resource"
	"github.com/hashicorp/terraform-plugin-testing/terraform"
)

// newTestGrant makes the grant resource with no retry delay, and with a client that follows the steps.
func newTestGrant(t *testing.T, steps []step) resource.Resource {
	t.Helper()

	testGrant := newGrantResource()
	testGrant.(*grantResource).backoff = client.Backoff{}

	return configured(t, testGrant, newStepClient(t, steps))
}

// grantConfig returns the model of a grant. The ID is the one that the test passes.
func grantConfig(id types.String) grantModel {
	return grantModel{
		ID:       id,
		Scope:    types.StringValue("sales.orders"),
		Subject:  types.StringValue("reader@example.org"),
		UnitType: types.StringValue("unit"),
		UnitID:   types.StringValue("42"),
	}
}

// The request and the row of the tests: a grant of the unit 42 on the schema sales.orders.
const (
	grantURI  = "/policy?scope=eq.sales.orders&subject=eq.reader%40example.org&unit_type=eq.unit&unit_id=eq.42"
	grantJSON = `{"scope":"sales.orders","subject":"reader@example.org","unit_type":"unit","unit_id":"42"}`
)

func TestGrantCreate(t *testing.T) {
	tests := []struct {
		name        string
		steps       []step
		wantSummary string
		wantDetail  string
	}{
		{name: "adds a row", steps: []step{{method: "POST", uri: "/policy", profile: "access", status: 201, body: grantJSON}}},
		{
			name:        "names the permission of a refused write",
			steps:       []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantSummary: "Couldn't create the grant",
			wantDetail:  "create_policy",
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			testGrant := newTestGrant(t, testCase.steps)
			s := resourceSchema(t, testGrant)
			plan := planOf(t, s, grantConfig(types.StringUnknown()))
			resp := resource.CreateResponse{State: nullState(s)}

			testGrant.Create(context.Background(), resource.CreateRequest{Plan: plan}, &resp)

			got := diagnosticsOf(resp.Diagnostics)

			if testCase.wantSummary != "" {
				if len(got) != 1 || got[0].summary != testCase.wantSummary || !strings.Contains(got[0].detail, testCase.wantDetail) {
					t.Fatalf("diagnostics = %+v, want %q with %q", got, testCase.wantSummary, testCase.wantDetail)
				}

				return
			}

			if len(got) != 0 {
				t.Fatalf("diagnostics = %+v, want none", got)
			}

			var state grantModel

			resp.State.Get(context.Background(), &state)

			if want := "sales.orders/reader@example.org/unit/42"; state.ID.ValueString() != want {
				t.Errorf("id = %q, want %q", state.ID.ValueString(), want)
			}
		})
	}
}

func TestGrantRead(t *testing.T) {
	tests := []struct {
		name        string
		steps       []step
		wantRemoved bool
		wantSummary string
	}{
		{name: "finds the row", steps: []step{{method: "GET", uri: grantURI, profile: "access", response: "[" + grantJSON + "]"}}},
		{name: "drops a row that no longer exists", steps: []step{{response: "[]"}}, wantRemoved: true},
		{
			name:        "reports a read that fails",
			steps:       []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantSummary: "Couldn't read the grant",
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			testGrant := newTestGrant(t, testCase.steps)
			s := resourceSchema(t, testGrant)
			state := stateOf(t, s, grantConfig(types.StringValue("sales.orders/reader@example.org/unit/42")))
			resp := resource.ReadResponse{State: state}

			testGrant.Read(context.Background(), resource.ReadRequest{State: state}, &resp)

			got := diagnosticsOf(resp.Diagnostics)

			switch {
			case testCase.wantSummary != "":
				if len(got) != 1 || got[0].summary != testCase.wantSummary {
					t.Fatalf("diagnostics = %+v, want %q", got, testCase.wantSummary)
				}
			case testCase.wantRemoved:
				if !resp.State.Raw.IsNull() {
					t.Errorf("state = %v, want it removed", resp.State.Raw)
				}
			case len(got) != 0:
				t.Fatalf("diagnostics = %+v, want none", got)
			}
		})
	}
}

func TestGrantDelete(t *testing.T) {
	tests := []struct {
		name        string
		steps       []step
		wantSummary string
	}{
		{name: "removes the row", steps: []step{{method: "DELETE", uri: grantURI, profile: "access", response: "[]"}}},
		{name: "reports a refused delete", steps: []step{{status: 403, response: `{"code":"42501","message":"denied"}`}}, wantSummary: "Couldn't delete the grant"},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			testGrant := newTestGrant(t, testCase.steps)
			s := resourceSchema(t, testGrant)
			state := stateOf(t, s, grantConfig(types.StringValue("x")))
			resp := resource.DeleteResponse{State: state}

			testGrant.Delete(context.Background(), resource.DeleteRequest{State: state}, &resp)

			got := summaries(diagnosticsOf(resp.Diagnostics))

			switch {
			case testCase.wantSummary != "" && (len(got) != 1 || got[0] != testCase.wantSummary):
				t.Fatalf("diagnostics = %v, want %q", got, testCase.wantSummary)
			case testCase.wantSummary == "" && len(got) != 0:
				t.Fatalf("diagnostics = %v, want none", got)
			}
		})
	}
}

func TestGrantScopePattern(t *testing.T) {
	tests := []struct {
		scope string
		ok    bool
	}{
		{"*", true},
		{"sales", true},
		{"sales.orders", true},
		{"sales.orders.items", true},
		{"sales.orders.Items-1", true},
		{"", false},
		{"Sales", false},
		{"sales.", false},
		{"sales.orders.items.more", false},
		{"sales.o rders", false},
		{"*.orders", false},
	}

	for _, testCase := range tests {
		t.Run(testCase.scope, func(t *testing.T) {
			if got := grantScope.MatchString(testCase.scope); got != testCase.ok {
				t.Errorf("grantScope.MatchString(%q) = %t, want %t", testCase.scope, got, testCase.ok)
			}
		})
	}
}

func TestParseGrantID(t *testing.T) {
	tests := []struct {
		name    string
		id      string
		want    client.Grant
		wantErr bool
	}{
		{name: "a unit", id: "s.t/me@example.org/unit/42", want: client.Grant{Scope: "s.t", Subject: "me@example.org", UnitType: "unit", UnitID: "42"}},
		{name: "every project", id: "*/svc/*/*", want: client.Grant{Scope: "*", Subject: "svc", UnitType: "*", UnitID: "*"}},
		{name: "a subject with slashes", id: "s/a/b/c/unit/7", want: client.Grant{Scope: "s", Subject: "a/b/c", UnitType: "unit", UnitID: "7"}},
		{name: "too few parts", id: "s/me", wantErr: true},
		{name: "no scope", id: "/me/unit/1", wantErr: true},
		{name: "a scope that is not one", id: "Bad/me/unit/1", wantErr: true},
		{name: "no unit", id: "s/svc//", wantErr: true},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			grant, err := parseGrantID(testCase.id)

			if testCase.wantErr {
				if err == nil {
					t.Fatalf("parseGrantID(%q) = %+v, want an error", testCase.id, grant)
				}

				return
			}

			if err != nil || grant != testCase.want {
				t.Errorf("parseGrantID(%q) = %+v, %v, want %+v", testCase.id, grant, err, testCase.want)
			}
		})
	}
}

func TestGrantImport(t *testing.T) {
	testGrant := newTestGrant(t, nil)
	s := resourceSchema(t, testGrant)
	importer := testGrant.(resource.ResourceWithImportState)

	t.Run("sets the parts of the ID", func(t *testing.T) {
		resp := resource.ImportStateResponse{State: nullState(s)}

		importer.ImportState(context.Background(), resource.ImportStateRequest{ID: "s.t/me@example.org/unit/42"}, &resp)

		var state grantModel

		resp.State.Get(context.Background(), &state)

		if state.Scope.ValueString() != "s.t" || state.Subject.ValueString() != "me@example.org" || state.UnitID.ValueString() != "42" {
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

func TestGrantUpdateNeverRuns(t *testing.T) {
	testGrant := newTestGrant(t, nil)
	resp := resource.UpdateResponse{}

	testGrant.Update(context.Background(), resource.UpdateRequest{}, &resp)

	if got := summaries(diagnosticsOf(resp.Diagnostics)); len(got) != 1 || got[0] != "A grant can't change" {
		t.Errorf("diagnostics = %v, want the error of an update", got)
	}
}

// TestAccGrantResource checks, with a real Terraform, that a grant is created, imported and destroyed.
func TestAccGrantResource(t *testing.T) {
	subject := testAccName() + "@example.org"
	scope := strings.Replace(testAccProvisionedSchema, "__", ".", 1)

	config := fmt.Sprintf(`
resource "data_proxy_grant" "this" {
  provider  = data-proxy
  scope     = %q
  subject   = %q
  unit_type = "unit"
  unit_id   = "1"
}
`, scope, subject)

	destroyed := func(*terraform.State) error {
		c, err := testAccClient()
		if err != nil {
			return err
		}

		rows, err := c.ListGrants(context.Background(), client.Grant{Scope: scope, Subject: subject, UnitType: "unit", UnitID: "1"})
		if err != nil {
			return err
		}

		if len(rows) != 0 {
			return fmt.Errorf("the grant of %s still exists after the destroy", subject)
		}

		return nil
	}

	acc.Test(t, acc.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories,
		CheckDestroy:             destroyed,
		Steps: []acc.TestStep{
			{
				Config: config,
				Check:  acc.TestCheckResourceAttr("data_proxy_grant.this", "id", fmt.Sprintf("%s/%s/unit/1", scope, subject)),
			},
			{ResourceName: "data_proxy_grant.this", ImportState: true, ImportStateVerify: true, ImportStateVerifyIdentifierAttribute: "id"},
		},
	})
}
