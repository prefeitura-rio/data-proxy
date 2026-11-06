package provider

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"reflect"
	"slices"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-go/tftypes"
	"github.com/hashicorp/terraform-plugin-testing/helper/resource"

	"terraform-provider-data-proxy/internal/client"
)

// staticToken is a TokenSource that returns itself as the token.
type staticToken string

// Token returns the string as the token.
func (s staticToken) Token(context.Context) (string, error) {
	return string(s), nil
}

// readPermissions runs the data source against a server that answers GET /rpc/permissions with the status and the
// body. It returns the sorted permissions of the state and the diagnostics.
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
		name   string
		status int
		body   string
		want   []string
		diags  []diagnostic
	}{
		{
			name:   "the permissions of the token",
			status: http.StatusOK,
			body:   `["read_api","read_sync","read_config"]`,
			want:   []string{"read_api", "read_config", "read_sync"},
		},
		{
			name:   "no permission",
			status: http.StatusOK,
			body:   `[]`,
			want:   []string{},
		},
		{
			name:   "a null result",
			status: http.StatusOK,
			body:   `null`,
			want:   []string{},
		},
		{
			name:   "the database refuses the function",
			status: http.StatusForbidden,
			body:   `{"code":"42501","message":"permission denied for function permissions"}`,
			diags: []diagnostic{{
				summary: "Couldn't read the permissions of the token",
				detail:  "The database refused the request. The API answered: read the permissions of the token: postgrest: HTTP 403 42501: permission denied for function permissions",
			}},
		},
		{
			name:   "the API refuses the token",
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
		t.Run(tt.name, func(t *testing.T) {
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
		name         string
		providerData any
		diags        []diagnostic
	}{
		{name: "no provider data before the provider is configured"},
		{
			name:         "provider data that isn't a client",
			providerData: "not a client",
			diags: []diagnostic{{
				summary: "Unexpected provider data",
				detail:  "The object wants a *client.Client, and it got string. Report this bug to the developers of the provider.",
			}},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
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

// deleteError returns the error of a DELETE that the server answers with the status and the body.
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
		name       string
		permission string
		err        error
		want       string
	}{
		{
			name:       "a refused write names its permission",
			permission: "create_config",
			err:        forbiddenErr,
			want: "The token lacks the create_config permission, or the schema isn't in the claim of the token. " +
				"Give the client a role that has create_config. The API answered: postgrest: HTTP 403 42501: permission denied for table schemas",
		},
		{
			name: "a refused request that needs no permission",
			err:  forbiddenErr,
			want: "The database refused the request. The API answered: postgrest: HTTP 403 42501: permission denied for table schemas",
		},
		{
			name:       "a refused token",
			permission: "edit_config",
			err:        unauthorizedErr,
			want:       "The API refused the token. Check that the API trusts the identity provider of token_url. The API answered: postgrest: HTTP 401 PGRST303: JWT expired",
		},
		{
			name:       "another error",
			permission: "delete_config",
			err:        errors.New("DELETE schemas: connection refused"),
			want:       "DELETE schemas: connection refused",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
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
		name        string
		permissions []string
		diags       []diagnostic
	}{
		{name: "read_config among others", permissions: []string{"read_api", "read_config", "create_config"}},
		{
			name:        "write permissions without read_config",
			permissions: []string{"create_config", "edit_config"},
			diags: []diagnostic{{
				summary: "Missing read_config permission",
				detail: `The token of the client "ci" lacks the read_config permission, and the provider needs it to read the configuration. ` +
					"Give the client a role that has read_config, such as dp_console or dp_admin. The token has these permissions: create_config, edit_config.",
			}},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := diagnosticsOf(checkReadConfig("ci", tt.permissions)); !reflect.DeepEqual(got, tt.diags) {
				t.Errorf("diagnostics = %+v, want %+v", got, tt.diags)
			}
		})
	}
}

func TestAccPermissionsDataSource(t *testing.T) {
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories,
		Steps: []resource.TestStep{
			{
				Config: `data "data_proxy_permissions" "this" { provider = data-proxy }`,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckTypeSetElemAttr("data.data_proxy_permissions.this", "permissions.*", "read_config"),
					resource.TestCheckTypeSetElemAttr("data.data_proxy_permissions.this", "permissions.*", "create_config"),
					resource.TestCheckTypeSetElemAttr("data.data_proxy_permissions.this", "permissions.*", "edit_config"),
					resource.TestCheckTypeSetElemAttr("data.data_proxy_permissions.this", "permissions.*", "delete_config"),
				),
			},
		},
	})
}
