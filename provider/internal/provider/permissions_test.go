package provider_test

import (
	"context"
	"data-proxy/internal/client"
	"data-proxy/internal/provider"
	"net/http"
	"net/http/httptest"
	"reflect"
	"slices"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-go/tftypes"
	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
)

// staticToken is a token source that always returns the same token.
type staticToken string

// Token returns the string as the token.
func (t staticToken) Token(context.Context) (string, error) {
	return string(t), nil
}

// readPermissions runs the data source against a server that answers GET /rpc/permissions with the status and the
// body. It returns the sorted permissions of the state and the diagnostics.
func readPermissions(t *testing.T, status int, body string) ([]string, []diagnostic) {
	t.Helper()

	ctx := context.Background()

	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, req *http.Request) {
		if req.Method != http.MethodGet || req.URL.RequestURI() != "/rpc/permissions" ||
			req.Header.Get("Accept-Profile") != "console" {
			t.Errorf(
				"request = %s %s with Accept-Profile %q, want GET /rpc/permissions with console",
				req.Method,
				req.URL.RequestURI(),
				req.Header.Get("Accept-Profile"),
			)
		}

		writer.Header().Set("Content-Type", "application/json")
		writer.WriteHeader(status)

		_, err := writer.Write([]byte(body))
		if err != nil {
			t.Errorf("write the response: %v", err)
		}
	}))
	t.Cleanup(server.Close)

	apiClient, err := client.New(server.URL, staticToken("jwt-123"), server.Client())
	if err != nil {
		t.Fatalf("client.New: %v", err)
	}

	dataSource, ok := provider.NewPermissionsDataSource().(datasource.DataSourceWithConfigure)
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
		return nil, collectDiagnostics(resp.Diagnostics)
	}

	var state provider.PermissionsModel

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

	return got, collectDiagnostics(resp.Diagnostics)
}

func TestPermissionsDataSourceRead(t *testing.T) {
	t.Parallel()

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
			diags: []diagnostic{
				{
					summary: "Couldn't read the permissions of the token",
					detail: "The database refused the request. The API answered: read the permissions of the token: postgrest: " +
						"HTTP 403 42501: permission denied for function permissions",
				},
			},
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

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			got, diags := readPermissions(t, testCase.status, testCase.body)

			if !reflect.DeepEqual(diags, testCase.diags) {
				t.Fatalf("diagnostics = %+v, want %+v", diags, testCase.diags)
			}

			if !reflect.DeepEqual(got, testCase.want) {
				t.Errorf("permissions = %q, want %q", got, testCase.want)
			}
		})
	}
}

func TestPermissionsDataSourceConfigure(t *testing.T) {
	t.Parallel()

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

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			dataSource := &provider.PermissionsDataSource{}

			var resp datasource.ConfigureResponse

			dataSource.Configure(context.Background(), datasource.ConfigureRequest{ProviderData: testCase.providerData}, &resp)

			if got := collectDiagnostics(resp.Diagnostics); !reflect.DeepEqual(got, testCase.diags) {
				t.Errorf("diagnostics = %+v, want %+v", got, testCase.diags)
			}

			if dataSource.HasClient() {
				t.Errorf("the data source has a client, want none")
			}
		})
	}
}

func TestPermissionsDataSourceReadWithoutClient(t *testing.T) {
	t.Parallel()

	var resp datasource.ReadResponse

	(&provider.PermissionsDataSource{}).Read(context.Background(), datasource.ReadRequest{}, &resp)

	want := []diagnostic{
		{summary: "Unconfigured provider", detail: "The data source has no client, because the provider isn't configured."},
	}
	if got := collectDiagnostics(resp.Diagnostics); !reflect.DeepEqual(got, want) {
		t.Errorf("diagnostics = %+v, want %+v", got, want)
	}
}

//nolint:paralleltest // Acceptance tests share one OpenTofu run.
func TestAccPermissionsDataSource(t *testing.T) {
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories(),
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
