package provider

import (
	"context"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"terraform-provider-data-proxy/internal/client"
)

// projectRowJSON is the row that the test server gives for the project sales.
const projectRowJSON = `{"name":"sales","updated_at":"2026-01-01T00:00:00Z","updated_by":"terraform"}`

// newTestProject returns the resource with a client that the steps drive, and no wait between reads.
func newTestProject(t *testing.T, steps []step) resource.Resource {
	t.Helper()

	res := newProjectResource()
	res.(*projectResource).backoff = client.Backoff{}

	return configured(t, res, newStepClient(t, steps))
}

// summaries returns the summary of each diagnostic.
func summaries(diagnostics []diagnostic) []string {
	list := make([]string, 0, len(diagnostics))
	for _, item := range diagnostics {
		list = append(list, item.summary)
	}

	return list
}

func TestProjectCreate(t *testing.T) {
	tests := []struct {
		name        string
		steps       []step
		wantSummary string
		wantDetail  string
	}{
		{
			name:  "adds the project and saves the row",
			steps: []step{{method: "POST", uri: "/projects", profile: "console", body: `{"name":"sales"}`, status: 201, response: "[" + projectRowJSON + "]"}},
		},
		{
			name:        "reports a project that exists",
			steps:       []step{{status: 409, response: `{"code":"23505","message":"duplicate key value"}`}},
			wantSummary: "Couldn't create the project",
			wantDetail:  "duplicate key value",
		},
		{
			name:        "names the permission that a refused write needs",
			steps:       []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantSummary: "Couldn't create the project",
			wantDetail:  "create_config",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			res := newTestProject(t, tt.steps)
			s := resourceSchema(t, res)
			plan := planOf(t, s, projectModel{Name: types.StringValue("sales"), ID: types.StringUnknown(), UpdatedAt: types.StringUnknown(), UpdatedBy: types.StringUnknown()})
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

			var state projectModel

			resp.State.Get(context.Background(), &state)

			if state.ID.ValueString() != "sales" || state.Name.ValueString() != "sales" || state.UpdatedBy.ValueString() != "terraform" {
				t.Errorf("state = %+v, want the row of sales", state)
			}
		})
	}
}

func TestProjectRead(t *testing.T) {
	tests := []struct {
		name        string
		steps       []step
		wantRemoved bool
		wantSummary string
	}{
		{name: "refreshes the row", steps: []step{{method: "GET", uri: "/projects?name=eq.sales", profile: "console", response: "[" + projectRowJSON + "]"}}},
		{name: "drops a project that no longer exists", steps: []step{{response: "[]"}}, wantRemoved: true},
		{
			name:        "reports a read that fails",
			steps:       []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantSummary: "Couldn't read the project",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			res := newTestProject(t, tt.steps)
			s := resourceSchema(t, res)
			state := stateOf(t, s, projectModel{ID: types.StringValue("sales"), Name: types.StringValue("sales"), UpdatedAt: types.StringValue("old"), UpdatedBy: types.StringValue("old")})
			resp := resource.ReadResponse{State: state}

			res.Read(context.Background(), resource.ReadRequest{State: state}, &resp)

			got := diagnosticsOf(resp.Diagnostics)

			switch {
			case tt.wantSummary != "":
				if len(got) != 1 || got[0].summary != tt.wantSummary {
					t.Fatalf("diagnostics = %+v, want %q", got, tt.wantSummary)
				}
			case tt.wantRemoved:
				if !resp.State.Raw.IsNull() {
					t.Errorf("state = %v, want it removed", resp.State.Raw)
				}
			default:
				var read projectModel

				resp.State.Get(context.Background(), &read)

				if read.UpdatedAt.ValueString() != "2026-01-01T00:00:00Z" {
					t.Errorf("updated_at = %q, want the refreshed value", read.UpdatedAt.ValueString())
				}
			}
		})
	}
}

func TestProjectDelete(t *testing.T) {
	tests := []struct {
		name        string
		steps       []step
		wantSummary string
		wantDetail  string
	}{
		{name: "removes the project", steps: []step{{method: "DELETE", uri: "/projects?name=eq.sales", profile: "console", response: "[]"}}},
		{
			name:        "shows why the database refuses a project with schemas",
			steps:       []step{{status: 400, response: `{"code":"23001","message":"update or delete on table projects violates RESTRICT","hint":"delete the schemas first"}`}},
			wantSummary: "Couldn't delete the project",
			wantDetail:  "delete the schemas first",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			res := newTestProject(t, tt.steps)
			s := resourceSchema(t, res)
			state := stateOf(t, s, projectModel{ID: types.StringValue("sales"), Name: types.StringValue("sales"), UpdatedAt: types.StringValue("t"), UpdatedBy: types.StringValue("u")})
			resp := resource.DeleteResponse{State: state}

			res.Delete(context.Background(), resource.DeleteRequest{State: state}, &resp)

			got := diagnosticsOf(resp.Diagnostics)

			if tt.wantSummary == "" {
				if len(got) != 0 {
					t.Fatalf("diagnostics = %+v, want none", got)
				}

				return
			}

			if len(got) != 1 || got[0].summary != tt.wantSummary || !strings.Contains(got[0].detail, tt.wantDetail) {
				t.Fatalf("diagnostics = %+v, want %q with %q", got, tt.wantSummary, tt.wantDetail)
			}
		})
	}
}

func TestProjectUpdateNeverRuns(t *testing.T) {
	res := newTestProject(t, nil)
	resp := resource.UpdateResponse{}

	res.Update(context.Background(), resource.UpdateRequest{}, &resp)

	if got := summaries(diagnosticsOf(resp.Diagnostics)); len(got) != 1 || got[0] != "A project can't change" {
		t.Errorf("diagnostics = %v, want the error of an update", got)
	}
}
