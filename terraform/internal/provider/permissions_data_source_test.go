package provider

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"slices"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/providerserver"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-go/tfprotov6"
	"github.com/hashicorp/terraform-plugin-go/tftypes"
	"github.com/hashicorp/terraform-plugin-testing/helper/resource"

	"github.com/prefeitura-rio/data-proxy/terraform/internal/client"
)

type staticToken string

func (s staticToken) Token(context.Context) (string, error) {
	return string(s), nil
}

// readPermissions runs the data source with a client of a server that answers GET /rpc/permissions with status
// and body. It returns the permissions in the state, sorted, and the diagnostics.
func readPermissions(t *testing.T, status int, body string) ([]string, []diagnostic) {
	t.Helper()

	ctx := context.Background()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.RequestURI() != "/rpc/permissions" || r.Header.Get("Accept-Profile") != "console" {
			t.Errorf("request = %s %s with Accept-Profile %q, want GET /rpc/permissions with console", r.Method, r.URL.RequestURI(), r.Header.Get("Accept-Profile"))
		}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)

		if _, err := w.Write([]byte(body)); err != nil {
			t.Errorf("write the response: %v", err)
		}
	}))
	t.Cleanup(server.Close)

	apiClient, err := client.New(server.URL, staticToken("jwt-123"), server.Client())
	if err != nil {
		t.Fatalf("client.New: %v", err)
	}

	dataSource, ok := newPermissionsDataSource().(datasource.DataSourceWithConfigure)
	if !ok {
		t.Fatalf("the data source doesn't implement Configure")
	}

	var configureResp datasource.ConfigureResponse

	dataSource.Configure(ctx, datasource.ConfigureRequest{ProviderData: apiClient}, &configureResp)

	if configureResp.Diagnostics.HasError() {
		t.Fatalf("Configure: %v", configureResp.Diagnostics)
	}

	var schemaResp datasource.SchemaResponse

	dataSource.Schema(ctx, datasource.SchemaRequest{}, &schemaResp)

	objectType := schemaResp.Schema.Type().TerraformType(ctx)
	setType := tftypes.Set{ElementType: tftypes.String}
	config := tftypes.NewValue(objectType, map[string]tftypes.Value{"permissions": tftypes.NewValue(setType, nil)})

	req := datasource.ReadRequest{Config: tfsdk.Config{Schema: schemaResp.Schema, Raw: config}}
	resp := datasource.ReadResponse{State: tfsdk.State{Schema: schemaResp.Schema, Raw: tftypes.NewValue(objectType, nil)}}

	dataSource.Read(ctx, req, &resp)

	if resp.Diagnostics.HasError() {
		return nil, diagnosticsOf(resp.Diagnostics)
	}

	var state permissionsModel

	if diagnostics := resp.State.Get(ctx, &state); diagnostics.HasError() {
		t.Fatalf("read the state: %v", diagnostics)
	}

	if state.Permissions.IsNull() || state.Permissions.IsUnknown() {
		t.Fatalf("permissions = %v, want a known set", state.Permissions)
	}

	got := []string{}

	if diagnostics := state.Permissions.ElementsAs(ctx, &got, false); diagnostics.HasError() {
		t.Fatalf("read the permissions: %v", diagnostics)
	}

	slices.Sort(got)

	return got, diagnosticsOf(resp.Diagnostics)
}

func TestPermissionsDataSourceRead(t *testing.T) {
	tests := []struct {
		id     string
		status int
		body   string
		want   []string
		diags  []diagnostic
	}{
		{
			id:     "the permissions of the token",
			status: http.StatusOK,
			body:   `["read_api","read_sync","read_config"]`,
			want:   []string{"read_api", "read_config", "read_sync"},
		},
		{
			id:     "no permission",
			status: http.StatusOK,
			body:   `[]`,
			want:   []string{},
		},
		{
			id:     "a null result",
			status: http.StatusOK,
			body:   `null`,
			want:   []string{},
		},
		{
			id:     "the database refuses the function",
			status: http.StatusForbidden,
			body:   `{"code":"42501","message":"permission denied for function permissions"}`,
			diags: []diagnostic{{
				summary: "Couldn't read the permissions of the token",
				detail:  "The database refused the request. The API answered: read the permissions of the token: postgrest: HTTP 403 42501: permission denied for function permissions",
			}},
		},
		{
			id:     "the API refuses the token",
			status: http.StatusUnauthorized,
			body:   `{"code":"PGRST303","message":"JWT expired"}`,
			diags: []diagnostic{{
				summary: "Couldn't read the permissions of the token",
				detail: "The API refused the token. Check that the API trusts the identity provider of token_url. " +
					"The API answered: read the permissions of the token: postgrest: HTTP 401 PGRST303: JWT expired",
			}},
		},
	}

	for _, tt := range tests {
		t.Run(tt.id, func(t *testing.T) {
			got, diags := readPermissions(t, tt.status, tt.body)

			if !reflect.DeepEqual(diags, tt.diags) {
				t.Fatalf("diagnostics = %+v, want %+v", diags, tt.diags)
			}

			if !reflect.DeepEqual(got, tt.want) {
				t.Errorf("permissions = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestPermissionsDataSourceConfigure(t *testing.T) {
	tests := []struct {
		id           string
		providerData any
		diags        []diagnostic
	}{
		{id: "no provider data before the provider is configured"},
		{
			id:           "provider data that isn't a client",
			providerData: "not a client",
			diags: []diagnostic{{
				summary: "Unexpected provider data",
				detail:  "The data source wants a *client.Client, and it got string. Report this bug to the developers of the provider.",
			}},
		},
	}

	for _, tt := range tests {
		t.Run(tt.id, func(t *testing.T) {
			dataSource := &permissionsDataSource{}

			var resp datasource.ConfigureResponse

			dataSource.Configure(context.Background(), datasource.ConfigureRequest{ProviderData: tt.providerData}, &resp)

			if got := diagnosticsOf(resp.Diagnostics); !reflect.DeepEqual(got, tt.diags) {
				t.Errorf("diagnostics = %+v, want %+v", got, tt.diags)
			}

			if dataSource.client != nil {
				t.Errorf("client = %v, want none", dataSource.client)
			}
		})
	}
}

func TestPermissionsDataSourceReadWithoutClient(t *testing.T) {
	var resp datasource.ReadResponse

	(&permissionsDataSource{}).Read(context.Background(), datasource.ReadRequest{}, &resp)

	want := []diagnostic{{summary: "Unconfigured provider", detail: "The data source has no client, because the provider isn't configured."}}
	if got := diagnosticsOf(resp.Diagnostics); !reflect.DeepEqual(got, want) {
		t.Errorf("diagnostics = %+v, want %+v", got, want)
	}
}

// deleteError returns the error of the client for a DELETE that the server answers with status and body.
func deleteError(t *testing.T, status int, body string) error {
	t.Helper()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)

		if _, err := w.Write([]byte(body)); err != nil {
			t.Errorf("write the response: %v", err)
		}
	}))
	t.Cleanup(server.Close)

	apiClient, err := client.New(server.URL, staticToken("jwt-123"), server.Client())
	if err != nil {
		t.Fatalf("client.New: %v", err)
	}

	err = apiClient.Delete(context.Background(), client.ConsoleProfile, "schemas", []client.Filter{client.Eq("name", "alpha")}, nil)
	if err == nil {
		t.Fatalf("Delete: got no error")
	}

	return err
}

func TestAddAPIError(t *testing.T) {
	forbiddenErr := deleteError(t, http.StatusForbidden, `{"code":"42501","message":"permission denied for table schemas"}`)
	unauthorizedErr := deleteError(t, http.StatusUnauthorized, `{"code":"PGRST303","message":"JWT expired"}`)

	tests := []struct {
		id         string
		permission string
		err        error
		want       string
	}{
		{
			id:         "a refused write names its permission",
			permission: "create_config",
			err:        forbiddenErr,
			want: "The token lacks the create_config permission, or the schema isn't in the claim of the token. " +
				"Give the client a role that has create_config. The API answered: postgrest: HTTP 403 42501: permission denied for table schemas",
		},
		{
			id:   "a refused request that needs no permission",
			err:  forbiddenErr,
			want: "The database refused the request. The API answered: postgrest: HTTP 403 42501: permission denied for table schemas",
		},
		{
			id:         "a refused token",
			permission: "edit_config",
			err:        unauthorizedErr,
			want:       "The API refused the token. Check that the API trusts the identity provider of token_url. The API answered: postgrest: HTTP 401 PGRST303: JWT expired",
		},
		{
			id:         "another error",
			permission: "delete_config",
			err:        errors.New("DELETE schemas: connection refused"),
			want:       "DELETE schemas: connection refused",
		},
	}

	for _, tt := range tests {
		t.Run(tt.id, func(t *testing.T) {
			var diagnostics diag.Diagnostics

			addAPIError(&diagnostics, "Couldn't delete the schema", tt.permission, tt.err)

			want := []diagnostic{{summary: "Couldn't delete the schema", detail: tt.want}}
			if got := diagnosticsOf(diagnostics); !reflect.DeepEqual(got, want) {
				t.Errorf("diagnostics = %+v, want %+v", got, want)
			}
		})
	}
}

func TestCheckReadConfig(t *testing.T) {
	tests := []struct {
		id          string
		permissions []string
		diags       []diagnostic
	}{
		{id: "read_config among others", permissions: []string{"read_api", "read_config", "create_config"}},
		{
			id:          "write permissions without read_config",
			permissions: []string{"create_config", "edit_config"},
			diags: []diagnostic{{
				summary: "Missing read_config permission",
				detail: `The token of the client "ci" lacks the read_config permission, and the provider needs it to read the configuration. ` +
					"Give the client a role that has read_config, such as dp_console or dp_admin. The token has these permissions: create_config, edit_config.",
			}},
		},
	}

	for _, tt := range tests {
		t.Run(tt.id, func(t *testing.T) {
			if got := diagnosticsOf(checkReadConfig("ci", tt.permissions)); !reflect.DeepEqual(got, tt.diags) {
				t.Errorf("diagnostics = %+v, want %+v", got, tt.diags)
			}
		})
	}
}

// TestAccPermissionsDataSource reads the permissions of the admin client of the local stack, whose role is
// dp_admin. It runs only with TF_ACC, and the DATAPROXY_* variables point the provider at the stack.
func TestAccPermissionsDataSource(t *testing.T) {
	resource.Test(t, resource.TestCase{
		PreCheck: func() {
			for _, name := range []string{EnvEndpoint, EnvTokenURL, EnvClientID, EnvClientSecret} {
				if os.Getenv(name) == "" {
					t.Fatalf("set %s for the acceptance tests", name)
				}
			}
		},
		ProtoV6ProviderFactories: map[string]func() (tfprotov6.ProviderServer, error){
			TypeName: providerserver.NewProtocol6WithError(New("test")()),
		},
		Steps: []resource.TestStep{
			{
				Config: `data "dataproxy_permissions" "this" {}`,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckTypeSetElemAttr("data.dataproxy_permissions.this", "permissions.*", "read_config"),
					resource.TestCheckTypeSetElemAttr("data.dataproxy_permissions.this", "permissions.*", "create_config"),
					resource.TestCheckTypeSetElemAttr("data.dataproxy_permissions.this", "permissions.*", "edit_config"),
					resource.TestCheckTypeSetElemAttr("data.dataproxy_permissions.this", "permissions.*", "delete_config"),
				),
			},
		},
	})
}
