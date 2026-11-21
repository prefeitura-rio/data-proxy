package provider

import (
	"context"
	"fmt"
	"io"
	"maps"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"regexp"
	"slices"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework-timeouts/resource/timeouts"
	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/datasource"
	datasourceschema "github.com/hashicorp/terraform-plugin-framework/datasource/schema"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/provider"
	"github.com/hashicorp/terraform-plugin-framework/providerserver"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	resourceschema "github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/hashicorp/terraform-plugin-go/tfprotov6"
	"github.com/hashicorp/terraform-plugin-go/tftypes"
	"github.com/hashicorp/terraform-plugin-testing/helper/acctest"
	acc "github.com/hashicorp/terraform-plugin-testing/helper/resource"

	"terraform-provider-data-proxy/internal/client"
)

// TestMetadata checks the type name and the version that the provider reports.
func TestMetadata(t *testing.T) {
	tests := []struct {
		name        string
		version     string
		wantType    string
		wantVersion string
	}{
		{name: "a release", version: "1.2.3", wantType: "data_proxy", wantVersion: "1.2.3"},
		{name: "a development build", version: "dev", wantType: "data_proxy", wantVersion: "dev"},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			var resp provider.MetadataResponse

			New(testCase.version)().Metadata(context.Background(), provider.MetadataRequest{}, &resp)

			if resp.TypeName != testCase.wantType {
				t.Errorf("TypeName = %q, want %q", resp.TypeName, testCase.wantType)
			}

			if resp.Version != testCase.wantVersion {
				t.Errorf("Version = %q, want %q", resp.Version, testCase.wantVersion)
			}
		})
	}
}

func TestSchemaServedOverProtocol(t *testing.T) {
	server, err := providerserver.NewProtocol6WithError(New("test")())()
	if err != nil {
		t.Fatalf("NewProtocol6WithError: %v", err)
	}

	resp, err := server.GetProviderSchema(context.Background(), &tfprotov6.GetProviderSchemaRequest{})
	if err != nil {
		t.Fatalf("GetProviderSchema: %v", err)
	}

	for _, diagnostic := range resp.Diagnostics {
		t.Errorf("diagnostic: %s: %s", diagnostic.Summary, diagnostic.Detail)
	}

	want := map[string]bool{
		"endpoint":      false,
		"token_url":     false,
		"client_id":     false,
		"client_secret": true,
		"scopes":        false,
	}

	got := map[string]bool{}

	for _, attribute := range resp.Provider.Block.Attributes {
		got[attribute.Name] = attribute.Sensitive

		if attribute.Required {
			t.Errorf("attribute %q is required, want optional", attribute.Name)
		}
	}

	if len(got) != len(want) {
		t.Errorf("attributes = %v, want %v", got, want)
	}

	for name, sensitive := range want {
		value, ok := got[name]
		if !ok {
			t.Errorf("attribute %q is missing", name)

			continue
		}

		if value != sensitive {
			t.Errorf("attribute %q sensitive = %t, want %t", name, value, sensitive)
		}
	}

	wantResources := []string{"data_proxy_grant", "data_proxy_project", "data_proxy_schema", "data_proxy_table"}
	wantSources := []string{
		"data_proxy_config_history", "data_proxy_export_config", "data_proxy_permissions", "data_proxy_project",
		"data_proxy_schema", "data_proxy_schema_health", "data_proxy_sync_config", "data_proxy_table",
		"data_proxy_table_sync_state",
	}

	if got := slices.Sorted(maps.Keys(resp.ResourceSchemas)); !slices.Equal(got, wantResources) {
		t.Errorf("resources = %v, want %v", got, wantResources)
	}

	if got := slices.Sorted(maps.Keys(resp.DataSourceSchemas)); !slices.Equal(got, wantSources) {
		t.Errorf("data sources = %v, want %v", got, wantSources)
	}

	permissions := resp.DataSourceSchemas["data_proxy_permissions"]

	if attributes := permissions.Block.Attributes; len(attributes) != 1 || attributes[0].Name != "permissions" || !attributes[0].Computed {
		t.Errorf("attributes of data_proxy_permissions = %v, want only the computed permissions", attributes)
	}
}

func model(endpoint, tokenURL, clientID, clientSecret *string, scopes []string) providerModel {
	text := func(value *string) types.String {
		if value == nil {
			return types.StringNull()
		}

		return types.StringValue(*value)
	}

	list := types.ListNull(types.StringType)

	if scopes != nil {
		elements := make([]attr.Value, 0, len(scopes))
		for _, scope := range scopes {
			elements = append(elements, types.StringValue(scope))
		}

		list = types.ListValueMust(types.StringType, elements)
	}

	return providerModel{
		Endpoint:     text(endpoint),
		TokenURL:     text(tokenURL),
		ClientID:     text(clientID),
		ClientSecret: text(clientSecret),
		Scopes:       list,
	}
}

func ptr[T any](value T) *T {
	return &value
}

type diagnostic struct {
	path    string
	summary string
	detail  string
}

func diagnosticsOf(diagnostics diag.Diagnostics) []diagnostic {
	var got []diagnostic

	for _, entry := range diagnostics {
		item := diagnostic{summary: entry.Summary(), detail: entry.Detail()}

		if withPath, ok := entry.(diag.DiagnosticWithPath); ok {
			item.path = withPath.Path().String()
		}

		got = append(got, item)
	}

	return got
}

var fullEnv = map[string]string{
	EnvEndpoint:     "https://env.example.com",
	EnvTokenURL:     "https://id.env.example.com/token",
	EnvClientID:     "env-client",
	EnvClientSecret: "env-secret",
	EnvScopes:       "openid, data-proxy\tprofile",
}

func TestReadSettings(t *testing.T) {
	hcl := model(ptr("https://hcl.example.com"), ptr("https://id.hcl.example.com/token"), ptr("hcl-client"), ptr("hcl-secret"), []string{"hcl"})
	hclSettings := settings{
		endpoint: "https://hcl.example.com",
		credentials: client.Credentials{
			TokenURL:     "https://id.hcl.example.com/token",
			ClientID:     "hcl-client",
			ClientSecret: "hcl-secret",
			Scopes:       []string{"hcl"},
		},
	}

	unknownEndpoint := hcl
	unknownEndpoint.Endpoint = types.StringUnknown()

	unknownScopes := hcl
	unknownScopes.Scopes = types.ListUnknown(types.StringType)

	tests := []struct {
		name  string
		model providerModel
		env   map[string]string
		want  settings
		diags []diagnostic
	}{
		{
			name:  "every attribute in the block",
			model: hcl,
			want:  hclSettings,
		},
		{
			name:  "every attribute from the environment",
			model: model(nil, nil, nil, nil, nil),
			env:   fullEnv,
			want: settings{
				endpoint: "https://env.example.com",
				credentials: client.Credentials{
					TokenURL:     "https://id.env.example.com/token",
					ClientID:     "env-client",
					ClientSecret: "env-secret",
					Scopes:       []string{"openid", "data-proxy", "profile"},
				},
			},
		},
		{
			name:  "the block takes precedence over the environment",
			model: hcl,
			env:   fullEnv,
			want:  hclSettings,
		},
		{
			name:  "the environment fills only what the block leaves out",
			model: model(ptr("https://hcl.example.com"), nil, ptr("hcl-client"), nil, nil),
			env:   fullEnv,
			want: settings{
				endpoint: "https://hcl.example.com",
				credentials: client.Credentials{
					TokenURL:     "https://id.env.example.com/token",
					ClientID:     "hcl-client",
					ClientSecret: "env-secret",
					Scopes:       []string{"openid", "data-proxy", "profile"},
				},
			},
		},
		{
			name:  "an empty scope list in the block takes precedence over the environment",
			model: model(ptr("https://hcl.example.com"), ptr("https://id.hcl.example.com/token"), ptr("hcl-client"), ptr("hcl-secret"), []string{}),
			env:   fullEnv,
			want: settings{
				endpoint: "https://hcl.example.com",
				credentials: client.Credentials{
					TokenURL:     "https://id.hcl.example.com/token",
					ClientID:     "hcl-client",
					ClientSecret: "hcl-secret",
					Scopes:       []string{},
				},
			},
		},
		{
			name:  "no scopes anywhere",
			model: model(ptr("https://hcl.example.com"), ptr("https://id.hcl.example.com/token"), ptr("hcl-client"), ptr("hcl-secret"), nil),
			want: settings{
				endpoint: "https://hcl.example.com",
				credentials: client.Credentials{
					TokenURL:     "https://id.hcl.example.com/token",
					ClientID:     "hcl-client",
					ClientSecret: "hcl-secret",
					Scopes:       []string{},
				},
			},
		},
		{
			name:  "every required value missing",
			model: model(nil, nil, nil, nil, nil),
			env:   map[string]string{EnvScopes: "openid"},
			want:  settings{credentials: client.Credentials{Scopes: []string{"openid"}}},
			diags: []diagnostic{
				{path: "endpoint", summary: "Missing endpoint", detail: "Set endpoint in the provider block or in the DATA_PROXY_ENDPOINT environment variable."},
				{path: "token_url", summary: "Missing token_url", detail: "Set token_url in the provider block or in the DATA_PROXY_TOKEN_URL environment variable."},
				{path: "client_id", summary: "Missing client_id", detail: "Set client_id in the provider block or in the DATA_PROXY_CLIENT_ID environment variable."},
				{path: "client_secret", summary: "Missing client_secret", detail: "Set client_secret in the provider block or in the DATA_PROXY_CLIENT_SECRET environment variable."},
			},
		},
		{
			name:  "an empty string in the block hides the environment",
			model: model(ptr("https://hcl.example.com"), ptr("https://id.hcl.example.com/token"), ptr("hcl-client"), ptr(""), []string{"hcl"}),
			env:   fullEnv,
			want: settings{
				endpoint: "https://hcl.example.com",
				credentials: client.Credentials{
					TokenURL: "https://id.hcl.example.com/token",
					ClientID: "hcl-client",
					Scopes:   []string{"hcl"},
				},
			},
			diags: []diagnostic{
				{path: "client_secret", summary: "Missing client_secret", detail: "Set client_secret in the provider block or in the DATA_PROXY_CLIENT_SECRET environment variable."},
			},
		},
		{
			name:  "an unknown endpoint",
			model: unknownEndpoint,
			env:   fullEnv,
			want: settings{
				credentials: hclSettings.credentials,
			},
			diags: []diagnostic{
				{path: "endpoint", summary: "Unknown endpoint", detail: "The provider can't start with an unknown endpoint. Give a known value, or leave it out and set DATA_PROXY_ENDPOINT."},
			},
		},
		{
			name:  "unknown scopes",
			model: unknownScopes,
			env:   fullEnv,
			want: settings{
				endpoint: hclSettings.endpoint,
				credentials: client.Credentials{
					TokenURL:     "https://id.hcl.example.com/token",
					ClientID:     "hcl-client",
					ClientSecret: "hcl-secret",
				},
			},
			diags: []diagnostic{
				{path: "scopes", summary: "Unknown scopes", detail: "The provider can't start with unknown scopes. Give known values, or leave them out and set DATA_PROXY_SCOPES."},
			},
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			getenv := func(name string) string { return testCase.env[name] }

			got, diagnostics := readSettings(context.Background(), testCase.model, getenv)

			if !reflect.DeepEqual(got, testCase.want) {
				t.Errorf("settings = %+v, want %+v", got, testCase.want)
			}

			if gotDiags := diagnosticsOf(diagnostics); !reflect.DeepEqual(gotDiags, testCase.diags) {
				t.Errorf("diagnostics = %+v, want %+v", gotDiags, testCase.diags)
			}
		})
	}
}

func TestSplitScopes(t *testing.T) {
	tests := []struct {
		name  string
		value string
		want  []string
	}{
		{name: "empty", value: "", want: []string{}},
		{name: "one scope", value: "openid", want: []string{"openid"}},
		{name: "commas", value: "openid,profile", want: []string{"openid", "profile"}},
		{name: "spaces", value: "openid profile", want: []string{"openid", "profile"}},
		{name: "commas, spaces and empty items", value: " openid, ,profile\n email ", want: []string{"openid", "profile", "email"}},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			if got := splitScopes(testCase.value); !reflect.DeepEqual(got, testCase.want) {
				t.Errorf("splitScopes(%q) = %q, want %q", testCase.value, got, testCase.want)
			}
		})
	}
}

func configure(t *testing.T, attributes map[string]tftypes.Value) provider.ConfigureResponse {
	t.Helper()

	ctx := context.Background()
	dataProxy := New("test")()

	var schemaResp provider.SchemaResponse

	dataProxy.Schema(ctx, provider.SchemaRequest{}, &schemaResp)

	objectType, ok := schemaResp.Schema.Type().TerraformType(ctx).(tftypes.Object)
	if !ok {
		t.Fatalf("the schema type isn't an object")
	}

	values := map[string]tftypes.Value{}
	for name, attributeType := range objectType.AttributeTypes {
		values[name] = tftypes.NewValue(attributeType, nil)
	}

	maps.Copy(values, attributes)

	req := provider.ConfigureRequest{Config: tfsdk.Config{Schema: schemaResp.Schema, Raw: tftypes.NewValue(objectType, values)}}

	var resp provider.ConfigureResponse

	dataProxy.Configure(ctx, req, &resp)

	return resp
}

type apiRequest struct {
	method         string
	uri            string
	acceptProfile  string
	contentProfile string
	authorization  string
}

type fakeAPI struct {
	tokenURL string
	endpoint string
	requests *[]apiRequest
}

// newFakeAPI starts a fake identity provider and a fake PostgREST API. The identity provider answers with
// tokenStatus and the token jwt-123, and the API answers GET /rpc/permissions with the status and the body.
func newFakeAPI(t *testing.T, tokenStatus, status int, body string) fakeAPI {
	t.Helper()

	identity := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(tokenStatus)

		response := `{"access_token":"jwt-123","token_type":"Bearer","expires_in":300}`
		if tokenStatus != http.StatusOK {
			response = `{"error":"unauthorized_client","error_description":"Invalid client or Invalid client credentials"}`
		}

		if _, err := w.Write([]byte(response)); err != nil {
			t.Errorf("write the token response: %v", err)
		}
	}))
	t.Cleanup(identity.Close)

	var requests []apiRequest

	api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests = append(requests, apiRequest{
			method:         r.Method,
			uri:            r.URL.RequestURI(),
			acceptProfile:  r.Header.Get("Accept-Profile"),
			contentProfile: r.Header.Get("Content-Profile"),
			authorization:  r.Header.Get("Authorization"),
		})

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)

		if _, err := w.Write([]byte(body)); err != nil {
			t.Errorf("write the API response: %v", err)
		}
	}))
	t.Cleanup(api.Close)

	return fakeAPI{tokenURL: identity.URL + "/token", endpoint: api.URL, requests: &requests}
}

func TestConfigure(t *testing.T) {
	text := func(value string) tftypes.Value { return tftypes.NewValue(tftypes.String, value) }

	const adminPermissions = `["read_api","read_config","create_config","edit_config","delete_config"]`

	tests := []struct {
		name        string
		tokenStatus int
		status      int
		body        string
		change      func(attributes map[string]tftypes.Value)
		env         bool
		diags       func(api fakeAPI) []diagnostic
		requests    int
	}{
		{
			name:     "a full provider block",
			status:   http.StatusOK,
			body:     adminPermissions,
			requests: 1,
		},
		{
			name:     "an empty provider block and the environment",
			status:   http.StatusOK,
			body:     `["read_config"]`,
			env:      true,
			requests: 1,
		},
		{
			name: "a missing client secret",
			change: func(attributes map[string]tftypes.Value) {
				attributes["client_secret"] = tftypes.NewValue(tftypes.String, nil)
			},
			diags: func(fakeAPI) []diagnostic {
				return []diagnostic{
					{path: "client_secret", summary: "Missing client_secret", detail: "Set client_secret in the provider block or in the DATA_PROXY_CLIENT_SECRET environment variable."},
				}
			},
		},
		{
			name:   "an endpoint without a scheme",
			change: func(attributes map[string]tftypes.Value) { attributes["endpoint"] = text("data-proxy.example.com") },
			diags: func(fakeAPI) []diagnostic {
				return []diagnostic{
					{path: "endpoint", summary: "Invalid Data Proxy endpoint", detail: `endpoint "data-proxy.example.com": the scheme must be http or https`},
				}
			},
		},
		{
			name:   "a token URL without a scheme",
			change: func(attributes map[string]tftypes.Value) { attributes["token_url"] = text("id.example.com/token") },
			diags: func(fakeAPI) []diagnostic {
				return []diagnostic{
					{summary: "Invalid Data Proxy credentials", detail: `token URL "id.example.com/token": the scheme must be http or https`},
				}
			},
		},
		{
			name:        "the identity provider refuses the secret",
			tokenStatus: http.StatusUnauthorized,
			diags: func(api fakeAPI) []diagnostic {
				return []diagnostic{{
					summary: "Couldn't check the Data Proxy token",
					detail: "read the permissions of the token: get the token: fetch a token from " + api.tokenURL +
						`: oauth2: "unauthorized_client" "Invalid client or Invalid client credentials"`,
				}}
			},
		},
		{
			name:   "the API refuses the token",
			status: http.StatusUnauthorized,
			body:   `{"code":"PGRST301","message":"JWSError JWSInvalidSignature"}`,
			diags: func(fakeAPI) []diagnostic {
				return []diagnostic{{
					summary: "Couldn't check the Data Proxy token",
					detail: "The API refused the token. Check that the API trusts the identity provider of token_url. " +
						"The API answered: read the permissions of the token: postgrest: HTTP 401 PGRST301: JWSError JWSInvalidSignature",
				}}
			},
			requests: 1,
		},
		{
			name:   "a token without read_config",
			status: http.StatusOK,
			body:   `["read_policy","create_policy"]`,
			diags: func(fakeAPI) []diagnostic {
				return []diagnostic{{
					summary: "Missing read_config permission",
					detail: `The token of the client "terraform" lacks the read_config permission, and the provider needs it to read the configuration. ` +
						"Give the client a role that has read_config, such as dp_console or dp_admin. The token has these permissions: read_policy, create_policy.",
				}}
			},
			requests: 1,
		},
		{
			name:   "a token without permissions",
			status: http.StatusOK,
			body:   `[]`,
			diags: func(fakeAPI) []diagnostic {
				return []diagnostic{{
					summary: "Missing read_config permission",
					detail: `The token of the client "terraform" lacks the read_config permission, and the provider needs it to read the configuration. ` +
						"Give the client a role that has read_config, such as dp_console or dp_admin. The token has these permissions: none.",
				}}
			},
			requests: 1,
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			tokenStatus := testCase.tokenStatus
			if tokenStatus == 0 {
				tokenStatus = http.StatusOK
			}

			api := newFakeAPI(t, tokenStatus, testCase.status, testCase.body)

			env := map[string]string{}
			attributes := map[string]tftypes.Value{
				"endpoint":      text(api.endpoint),
				"token_url":     text(api.tokenURL),
				"client_id":     text("terraform"),
				"client_secret": text("secret-123"),
				"scopes":        tftypes.NewValue(tftypes.List{ElementType: tftypes.String}, []tftypes.Value{text("openid")}),
			}

			if testCase.env {
				attributes = map[string]tftypes.Value{}
				env = map[string]string{
					EnvEndpoint:     api.endpoint,
					EnvTokenURL:     api.tokenURL,
					EnvClientID:     "env-client",
					EnvClientSecret: "env-secret",
					EnvScopes:       "openid profile",
				}
			}

			if testCase.change != nil {
				testCase.change(attributes)
			}

			for _, name := range []string{EnvEndpoint, EnvTokenURL, EnvClientID, EnvClientSecret, EnvScopes} {
				t.Setenv(name, env[name])
			}

			resp := configure(t, attributes)

			var want []diagnostic
			if testCase.diags != nil {
				want = testCase.diags(api)
			}

			if got := diagnosticsOf(resp.Diagnostics); !reflect.DeepEqual(got, want) {
				t.Fatalf("diagnostics = %+v, want %+v", got, want)
			}

			if len(*api.requests) != testCase.requests {
				t.Fatalf("the API got %d requests, want %d", len(*api.requests), testCase.requests)
			}

			for _, request := range *api.requests {
				wantRequest := apiRequest{method: http.MethodGet, uri: "/rpc/permissions", acceptProfile: "console", authorization: "Bearer jwt-123"}
				if request != wantRequest {
					t.Errorf("request = %+v, want %+v", request, wantRequest)
				}
			}

			if want != nil {
				if resp.ResourceData != nil || resp.DataSourceData != nil {
					t.Errorf("provider data = %v and %v, want none after an error", resp.ResourceData, resp.DataSourceData)
				}

				return
			}

			resourceClient, ok := resp.ResourceData.(*client.Client)
			if !ok || resourceClient == nil {
				t.Fatalf("ResourceData = %T, want a *client.Client", resp.ResourceData)
			}

			if resp.DataSourceData != resourceClient {
				t.Errorf("DataSourceData = %v, want the client of ResourceData", resp.DataSourceData)
			}
		})
	}
}

// testAccNamePrefix starts every name that an acceptance test creates, so a row that a test left behind is easy to find.
const testAccNamePrefix = "tfacc"

// testAccProviderName is the local name of the provider in the test configurations. A local name has no underscore,
// so it differs from TypeName, and each resource of a test names it with provider = data-proxy.
const testAccProviderName = "data-proxy"

// testAccProviderFactories serves the provider inside the test process, so Terraform needs no provider binary.
var testAccProviderFactories = map[string]func() (tfprotov6.ProviderServer, error){
	testAccProviderName: providerserver.NewProtocol6WithError(New("test")()),
}

// testAccPreCheck fails the test when a required DATA_PROXY_* variable is empty.
func testAccPreCheck(t *testing.T) {
	t.Helper()

	for _, name := range []string{EnvEndpoint, EnvTokenURL, EnvClientID, EnvClientSecret} {
		if os.Getenv(name) == "" {
			t.Fatalf("set %s for the acceptance tests", name)
		}
	}
}

// testAccName returns a new name for an object that a test creates. The name is valid for a project and for a schema.
func testAccName() string {
	return testAccNamePrefix + acctest.RandStringFromCharSet(10, acctest.CharSetAlphaNum)
}

// testAccClient returns a client with the credentials of the DATA_PROXY_* variables.
func testAccClient() (*client.Client, error) {
	tokens, err := client.NewCredentialsSource(client.Credentials{
		TokenURL:     os.Getenv(EnvTokenURL),
		ClientID:     os.Getenv(EnvClientID),
		ClientSecret: os.Getenv(EnvClientSecret),
		Scopes:       splitScopes(os.Getenv(EnvScopes)),
	}, nil)
	if err != nil {
		return nil, fmt.Errorf("build the token source: %w", err)
	}

	apiClient, err := client.New(os.Getenv(EnvEndpoint), tokens, nil)
	if err != nil {
		return nil, fmt.Errorf("build the client: %w", err)
	}

	return apiClient, nil
}

// testAccCleanup deletes the rows of table that match every filter when the test ends. Cleanups run in reverse
// order, so register a parent, such as a project, before its children.
func testAccCleanup(t testing.TB, table string, filters ...client.Filter) {
	t.Helper()

	if os.Getenv(acc.EnvTfAcc) == "" {
		return
	}

	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), client.DefaultTimeout)
		defer cancel()

		apiClient, err := testAccClient()
		if err != nil {
			t.Errorf("clean up %s: %v", table, err)

			return
		}

		if err := apiClient.Delete(ctx, client.ConsoleProfile, table, filters, nil); err != nil {
			t.Errorf("clean up %s: %v", table, err)
		}
	})
}

// TestTestAccName checks that each name fits the name rules of the database and that two names differ.
func TestTestAccName(t *testing.T) {
	// These patterns copy the name checks of console.projects and console.schemas in migration 00012.
	tests := []struct {
		name    string
		pattern *regexp.Regexp
	}{
		{name: "the shape of a test name", pattern: regexp.MustCompile(`^tfacc[a-z0-9]{10}$`)},
		{name: "the name of a project", pattern: regexp.MustCompile(`^[a-z][a-z0-9]{0,29}$`)},
		{name: "the name of a schema", pattern: regexp.MustCompile(`^[a-z][a-z0-9_]{0,55}$`)},
	}

	first, second := testAccName(), testAccName()

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			for _, name := range []string{first, second} {
				if !testCase.pattern.MatchString(name) {
					t.Errorf("testAccName() = %q, want a match of %s", name, testCase.pattern)
				}
			}
		})
	}

	t.Run("two names differ", func(t *testing.T) {
		if first == second {
			t.Errorf("testAccName() returned %q twice", first)
		}
	})
}

// cleanupRecorder is a testing.TB that keeps the cleanups and the errors of testAccCleanup.
type cleanupRecorder struct {
	testing.TB

	cleanups []func()
	errors   []string
}

// Helper does nothing.
func (r *cleanupRecorder) Helper() {}

// Cleanup keeps the cleanup so that the test can run it.
func (r *cleanupRecorder) Cleanup(cleanup func()) {
	r.cleanups = append(r.cleanups, cleanup)
}

// Errorf keeps the error so that the test can read it.
func (r *cleanupRecorder) Errorf(format string, args ...any) {
	r.errors = append(r.errors, fmt.Sprintf(format, args...))
}

func TestTestAccCleanup(t *testing.T) {
	tests := []struct {
		name        string
		acceptance  bool
		tokenStatus int
		status      int
		body        string
		requests    []apiRequest
		errors      func(api fakeAPI) []string
	}{
		{
			name:   "no cleanup without TF_ACC",
			status: http.StatusOK,
			body:   `[]`,
		},
		{
			name:       "a delete of the matching rows",
			acceptance: true,
			status:     http.StatusOK,
			body:       `[{"name":"tfaccabc"}]`,
			requests:   []apiRequest{{method: http.MethodDelete, uri: "/schemas?name=eq.tfaccabc", contentProfile: "console", authorization: "Bearer jwt-123"}},
		},
		{
			name:       "no row left to delete",
			acceptance: true,
			status:     http.StatusOK,
			body:       `[]`,
			requests:   []apiRequest{{method: http.MethodDelete, uri: "/schemas?name=eq.tfaccabc", contentProfile: "console", authorization: "Bearer jwt-123"}},
		},
		{
			name:       "the database refuses the delete",
			acceptance: true,
			status:     http.StatusForbidden,
			body:       `{"code":"42501","message":"permission denied for table schemas"}`,
			requests:   []apiRequest{{method: http.MethodDelete, uri: "/schemas?name=eq.tfaccabc", contentProfile: "console", authorization: "Bearer jwt-123"}},
			errors: func(fakeAPI) []string {
				return []string{"clean up schemas: postgrest: HTTP 403 42501: permission denied for table schemas"}
			},
		},
		{
			name:        "the identity provider refuses the secret",
			acceptance:  true,
			tokenStatus: http.StatusUnauthorized,
			errors: func(api fakeAPI) []string {
				return []string{"clean up schemas: get the token: fetch a token from " + api.tokenURL +
					`: oauth2: "unauthorized_client" "Invalid client or Invalid client credentials"`}
			},
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			tokenStatus := testCase.tokenStatus
			if tokenStatus == 0 {
				tokenStatus = http.StatusOK
			}

			api := newFakeAPI(t, tokenStatus, testCase.status, testCase.body)

			acceptance := ""
			if testCase.acceptance {
				acceptance = "1"
			}

			t.Setenv(acc.EnvTfAcc, acceptance)
			t.Setenv(EnvEndpoint, api.endpoint)
			t.Setenv(EnvTokenURL, api.tokenURL)
			t.Setenv(EnvClientID, "terraform")
			t.Setenv(EnvClientSecret, "secret-123")
			t.Setenv(EnvScopes, "")

			recorder := &cleanupRecorder{TB: t}

			testAccCleanup(recorder, "schemas", client.Eq("name", "tfaccabc"))

			wantCleanups := 0
			if testCase.acceptance {
				wantCleanups = 1
			}

			if len(recorder.cleanups) != wantCleanups {
				t.Fatalf("testAccCleanup registered %d cleanups, want %d", len(recorder.cleanups), wantCleanups)
			}

			if len(*api.requests) != 0 {
				t.Fatalf("the API got %d requests before the cleanup, want none", len(*api.requests))
			}

			for _, cleanup := range recorder.cleanups {
				cleanup()
			}

			if !reflect.DeepEqual(*api.requests, testCase.requests) {
				t.Errorf("requests = %+v, want %+v", *api.requests, testCase.requests)
			}

			var wantErrors []string
			if testCase.errors != nil {
				wantErrors = testCase.errors(api)
			}

			if !reflect.DeepEqual(recorder.errors, wantErrors) {
				t.Errorf("errors = %q, want %q", recorder.errors, wantErrors)
			}
		})
	}
}

// testAccProvisionedSchema is a schema that the console stack provisions at start, so its access policy table exists.
const testAccProvisionedSchema = "default__test"

// projectConfig returns the configuration of a project.
func projectConfig(name string) string {
	return fmt.Sprintf(`
resource "data_proxy_project" "this" {
  provider = data-proxy
  name     = %q
}
`, name)
}

// schemaConfig returns the configuration of a project and a schema in it. The timeouts of 0s skip the wait for the
// sync, which the console stack doesn't run.
func schemaConfig(project, shortName, claim string) string {
	return projectConfig(project) + fmt.Sprintf(`
resource "data_proxy_schema" "this" {
  provider            = data-proxy
  project             = data_proxy_project.this.name
  short_name          = %q
  claim               = %q
  deletion_protection = false

  timeouts {
    create = "0s"
    delete = "0s"
  }
}
`, shortName, claim)
}

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
