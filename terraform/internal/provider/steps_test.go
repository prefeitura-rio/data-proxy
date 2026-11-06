package provider

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework-timeouts/resource/timeouts"
	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/datasource"
	datasourceschema "github.com/hashicorp/terraform-plugin-framework/datasource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	resourceschema "github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/hashicorp/terraform-plugin-go/tftypes"

	"terraform-provider-data-proxy/internal/client"
)

// step is one request that a test server expects, and the answer that it gives. An empty field is not checked.
type step struct {
	method   string
	uri      string
	profile  string
	body     string
	status   int
	response string
}

// newStepClient starts a server that expects the steps in order and returns a client for it. A request that differs
// from its step fails the test, and so does a step that no request reached.
func newStepClient(t *testing.T, steps []step) *client.Client {
	t.Helper()

	next := 0

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if next >= len(steps) {
			t.Errorf("unexpected request %s %s", r.Method, r.URL.RequestURI())
			w.WriteHeader(http.StatusInternalServerError)

			return
		}

		want := steps[next]
		next++

		raw, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("read the request body: %v", err)
		}

		checkStep(t, want, r, raw)

		status := want.status
		if status == 0 {
			status = http.StatusOK
		}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)

		if _, err := w.Write([]byte(want.response)); err != nil {
			t.Errorf("write the response: %v", err)
		}
	}))
	t.Cleanup(func() {
		server.Close()

		if next != len(steps) {
			t.Errorf("the server got %d requests, want %d", next, len(steps))
		}
	})

	apiClient, err := client.New(server.URL, staticToken("jwt-123"), server.Client())
	if err != nil {
		t.Fatalf("client.New: %v", err)
	}

	return apiClient
}

// checkStep compares a request with the step that it should match.
func checkStep(t *testing.T, want step, r *http.Request, body []byte) {
	t.Helper()

	if want.method != "" && r.Method != want.method {
		t.Errorf("method = %s, want %s", r.Method, want.method)
	}

	if want.uri != "" && r.URL.RequestURI() != want.uri {
		t.Errorf("uri = %s, want %s", r.URL.RequestURI(), want.uri)
	}

	profile := r.Header.Get("Accept-Profile") + r.Header.Get("Content-Profile")
	if want.profile != "" && profile != want.profile {
		t.Errorf("profile = %q, want %q", profile, want.profile)
	}

	if want.body != "" && !sameJSON(want.body, string(body)) {
		t.Errorf("body = %s, want %s", body, want.body)
	}
}

// timeoutAttributes are the types of the timeouts attribute of a resource.
var timeoutAttributes = map[string]attr.Type{"create": types.StringType, "delete": types.StringType}

// timeoutsOf builds the timeouts attribute of a model. An empty text leaves its timeout null.
func timeoutsOf(create, deleted string) timeouts.Value {
	text := func(value string) attr.Value {
		if value == "" {
			return types.StringNull()
		}

		return types.StringValue(value)
	}

	return timeouts.Value{Object: types.ObjectValueMust(timeoutAttributes, map[string]attr.Value{"create": text(create), "delete": text(deleted)})}
}

// resourceSchema returns the schema of a resource.
func resourceSchema(t *testing.T, res resource.Resource) resourceschema.Schema {
	t.Helper()

	var resp resource.SchemaResponse

	res.Schema(context.Background(), resource.SchemaRequest{}, &resp)

	if resp.Diagnostics.HasError() {
		t.Fatalf("Schema: %v", resp.Diagnostics)
	}

	return resp.Schema
}

// configured returns the resource with the client of the test.
func configured(t *testing.T, res resource.Resource, apiClient *client.Client) resource.Resource {
	t.Helper()

	withClient, ok := res.(resource.ResourceWithConfigure)
	if !ok {
		t.Fatalf("the resource doesn't implement Configure")
	}

	var resp resource.ConfigureResponse

	withClient.Configure(context.Background(), resource.ConfigureRequest{ProviderData: apiClient}, &resp)

	if resp.Diagnostics.HasError() {
		t.Fatalf("Configure: %v", resp.Diagnostics)
	}

	return res
}

// nullState returns an empty state of the schema, as a create starts with.
func nullState(s resourceschema.Schema) tfsdk.State {
	return tfsdk.State{Schema: s, Raw: tftypes.NewValue(s.Type().TerraformType(context.Background()), nil)}
}

// stateOf returns a state of the schema that holds the model.
func stateOf(t *testing.T, s resourceschema.Schema, model any) tfsdk.State {
	t.Helper()

	state := nullState(s)

	if diagnostics := state.Set(context.Background(), model); diagnostics.HasError() {
		t.Fatalf("build the state: %v", diagnostics)
	}

	return state
}

// planOf returns a plan of the schema that holds the model.
func planOf(t *testing.T, s resourceschema.Schema, model any) tfsdk.Plan {
	t.Helper()

	plan := tfsdk.Plan{Schema: s, Raw: tftypes.NewValue(s.Type().TerraformType(context.Background()), nil)}

	if diagnostics := plan.Set(context.Background(), model); diagnostics.HasError() {
		t.Fatalf("build the plan: %v", diagnostics)
	}

	return plan
}

// dataSourceSchema returns the schema of a data source.
func dataSourceSchema(t *testing.T, source datasource.DataSource) datasourceschema.Schema {
	t.Helper()

	var resp datasource.SchemaResponse

	source.Schema(context.Background(), datasource.SchemaRequest{}, &resp)

	if resp.Diagnostics.HasError() {
		t.Fatalf("Schema: %v", resp.Diagnostics)
	}

	return resp.Schema
}

// readDataSource runs a data source on a config model and returns its diagnostics and its state.
func readDataSource(t *testing.T, source datasource.DataSource, apiClient *client.Client, config any) (datasource.ReadResponse, []diagnostic) {
	t.Helper()

	ctx := context.Background()
	s := dataSourceSchema(t, source)

	withClient, ok := source.(datasource.DataSourceWithConfigure)
	if !ok {
		t.Fatalf("the data source doesn't implement Configure")
	}

	var configureResp datasource.ConfigureResponse

	withClient.Configure(ctx, datasource.ConfigureRequest{ProviderData: apiClient}, &configureResp)

	if configureResp.Diagnostics.HasError() {
		t.Fatalf("Configure: %v", configureResp.Diagnostics)
	}

	raw := tftypes.NewValue(s.Type().TerraformType(ctx), nil)
	cfg := tfsdk.Config{Schema: s, Raw: raw}

	// A config is a state of the same schema for the purpose of building it from a model.
	state := tfsdk.State{Schema: s, Raw: raw}

	if diagnostics := state.Set(ctx, config); diagnostics.HasError() {
		t.Fatalf("build the config: %v", diagnostics)
	}

	cfg.Raw = state.Raw

	resp := datasource.ReadResponse{State: tfsdk.State{Schema: s, Raw: raw}}

	source.Read(ctx, datasource.ReadRequest{Config: cfg}, &resp)

	return resp, diagnosticsOf(resp.Diagnostics)
}
