package provider_test

import (
	"context"
	"regexp"
	"strings"
	"terraform-provider-data-proxy/internal/client"
	"terraform-provider-data-proxy/internal/provider"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"
	acc "github.com/hashicorp/terraform-plugin-testing/helper/resource"
)

// projectRowJSON is the row that the fake API answers for the project sales.
const projectRowJSON = `{"name":"sales","updated_at":"2026-01-01T00:00:00Z","updated_by":"terraform"}`

// newTestProject makes the project resource with no retry delay, and with a client that follows the steps.
func newTestProject(t *testing.T, steps []step) resource.Resource {
	t.Helper()

	testProject := provider.ProjectResourceWithNoRetry()

	return configured(t, testProject, newStepClient(t, steps))
}

// summaries returns the summary text of each diagnostic.
func summaries(diagnostics []diagnostic) []string {
	list := make([]string, 0, len(diagnostics))
	for _, item := range diagnostics {
		list = append(list, item.summary)
	}

	return list
}

func TestProjectCreate(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name        string
		steps       []step
		wantSummary string
		wantDetail  string
	}{
		{
			name: "adds the project and saves the row",
			steps: []step{
				{
					method:   "POST",
					uri:      "/projects",
					profile:  "console",
					body:     `{"name":"sales"}`,
					status:   201,
					response: "[" + projectRowJSON + "]",
				},
			},
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

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			testProject := newTestProject(t, testCase.steps)
			projectSchema := resourceSchema(t, testProject)
			plan := planOf(
				t,
				projectSchema,
				provider.ProjectModel{
					Name:      types.StringValue("sales"),
					ID:        types.StringUnknown(),
					UpdatedAt: types.StringUnknown(),
					UpdatedBy: types.StringUnknown(),
				},
			)
			resp := resource.CreateResponse{State: nullState(projectSchema)}

			testProject.Create(context.Background(), resource.CreateRequest{Plan: plan}, &resp)

			got := diagnosticsOf(resp.Diagnostics)

			if testCase.wantSummary != "" {
				if len(got) != 1 || got[0].summary != testCase.wantSummary ||
					!strings.Contains(got[0].detail, testCase.wantDetail) {
					t.Fatalf("diagnostics = %+v, want %q with %q", got, testCase.wantSummary, testCase.wantDetail)
				}

				return
			}

			if len(got) != 0 {
				t.Fatalf("diagnostics = %+v, want none", got)
			}

			var state provider.ProjectModel

			resp.State.Get(context.Background(), &state)

			if state.ID.ValueString() != "sales" || state.Name.ValueString() != "sales" ||
				state.UpdatedBy.ValueString() != "terraform" {
				t.Errorf("state = %+v, want the row of sales", state)
			}
		})
	}
}

func TestProjectRead(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name        string
		steps       []step
		wantRemoved bool
		wantSummary string
	}{
		{
			name: "refreshes the row",
			steps: []step{
				{method: "GET", uri: "/projects?name=eq.sales", profile: "console", response: "[" + projectRowJSON + "]"},
			},
		},
		{name: "drops a project that no longer exists", steps: []step{{response: "[]"}}, wantRemoved: true},
		{
			name:        "reports a read that fails",
			steps:       []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantSummary: "Couldn't read the project",
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			testProject := newTestProject(t, testCase.steps)
			s := resourceSchema(t, testProject)
			state := stateOf(
				t,
				s,
				provider.ProjectModel{
					ID:        types.StringValue("sales"),
					Name:      types.StringValue("sales"),
					UpdatedAt: types.StringValue("old"),
					UpdatedBy: types.StringValue("old"),
				},
			)
			resp := resource.ReadResponse{State: state}

			testProject.Read(context.Background(), resource.ReadRequest{State: state}, &resp)

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
			default:
				var read provider.ProjectModel

				resp.State.Get(context.Background(), &read)

				if read.UpdatedAt.ValueString() != "2026-01-01T00:00:00Z" {
					t.Errorf("updated_at = %q, want the refreshed value", read.UpdatedAt.ValueString())
				}
			}
		})
	}
}

func TestProjectDelete(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name        string
		steps       []step
		wantSummary string
		wantDetail  string
	}{
		{
			name:  "removes the project",
			steps: []step{{method: "DELETE", uri: "/projects?name=eq.sales", profile: "console", response: "[]"}},
		},
		{
			name: "shows why the database refuses a project with schemas",
			steps: []step{
				{
					status: 400,
					response: `{"code":"23001","message":"update or delete on table projects violates RESTRICT","hint":"delete ` +
						`the schemas first"}`,
				},
			},
			wantSummary: "Couldn't delete the project",
			wantDetail:  "delete the schemas first",
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			testProject := newTestProject(t, testCase.steps)
			s := resourceSchema(t, testProject)
			state := stateOf(
				t,
				s,
				provider.ProjectModel{
					ID:        types.StringValue("sales"),
					Name:      types.StringValue("sales"),
					UpdatedAt: types.StringValue("t"),
					UpdatedBy: types.StringValue("u"),
				},
			)
			resp := resource.DeleteResponse{State: state}

			testProject.Delete(context.Background(), resource.DeleteRequest{State: state}, &resp)

			got := diagnosticsOf(resp.Diagnostics)

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

// TestProjectImportReadsByName checks that the import id becomes the name of the project.
func TestProjectImportReadsByName(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		id   string
	}{
		{name: "a plain name", id: "sales"},
		{name: "a name with a dash", id: "id-carioca"},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			res := newTestProject(t, nil)
			s := resourceSchema(t, res)
			resp := resource.ImportStateResponse{State: nullState(s)}

			as[resource.ResourceWithImportState](t, res).ImportState(
				context.Background(),
				resource.ImportStateRequest{ID: testCase.id},
				&resp,
			)

			var state provider.ProjectModel

			resp.State.Get(context.Background(), &state)

			if state.Name.ValueString() != testCase.id {
				t.Errorf("name = %q, want %q", state.Name.ValueString(), testCase.id)
			}
		})
	}
}

// TestProjectUpdateNeverRuns checks that an update gives one error, because a project has nothing that can change.
func TestProjectUpdateNeverRuns(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
	}{
		{name: "an empty request"},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			res := newTestProject(t, nil)
			resp := resource.UpdateResponse{}

			res.Update(context.Background(), resource.UpdateRequest{}, &resp)

			if got := summaries(diagnosticsOf(resp.Diagnostics)); len(got) != 1 || got[0] != "A project can't change" {
				t.Errorf("diagnostics = %v, want the error of an update", got)
			}
		})
	}
}

//nolint:paralleltest // Acceptance tests share one OpenTofu run.
func TestAccProjectResource(t *testing.T) {
	name := testAccName()
	testAccCleanup(t, "projects", client.Eq("name", name))

	acc.Test(t, acc.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories(),
		Steps: []acc.TestStep{
			{
				Config: projectConfig(name),
				Check: acc.ComposeAggregateTestCheckFunc(
					acc.TestCheckResourceAttr("data_proxy_project.this", "id", name),
					acc.TestCheckResourceAttrSet("data_proxy_project.this", "updated_by"),
				),
			},
			{ResourceName: "data_proxy_project.this", ImportState: true, ImportStateVerify: true},
		},
	})
}

//nolint:paralleltest // Acceptance tests share one OpenTofu run.
func TestAccProjectResourceRefusesTheDefaultName(t *testing.T) {
	acc.Test(t, acc.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories(),
		Steps: []acc.TestStep{
			{Config: projectConfig("default"), ExpectError: regexp.MustCompile(`value must be none of`)},
		},
	})
}

//nolint:paralleltest // Acceptance tests share one OpenTofu run.
func TestAccProjectResourceRefusesToDeleteAProjectWithSchemas(t *testing.T) {
	name := testAccName()
	testAccCleanup(t, "projects", client.Eq("name", name))
	testAccCleanup(t, "schemas", client.Eq("project", name))

	acc.Test(t, acc.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories(),
		Steps: []acc.TestStep{
			{Config: projectConfig(name)},
			{
				PreConfig: func() {
					apiClient, err := testAccClient()
					if err != nil {
						t.Fatalf("testAccClient: %v", err)
					}

					_, err = apiClient.CreateSchema(
						context.Background(),
						client.SchemaInput{Project: name, ShortName: "orders"},
					)
					if err != nil {
						t.Fatalf("add a schema outside Terraform: %v", err)
					}
				},
				Config:      `# the project leaves the configuration`,
				ExpectError: regexp.MustCompile(`Couldn't delete the project`),
			},
			{
				PreConfig: func() {
					apiClient, err := testAccClient()
					if err != nil {
						t.Fatalf("testAccClient: %v", err)
					}

					err = apiClient.DeleteSchema(context.Background(), name+"__orders")
					if err != nil {
						t.Fatalf("delete the schema outside Terraform: %v", err)
					}
				},
				Config: projectConfig(name),
			},
		},
	})
}
