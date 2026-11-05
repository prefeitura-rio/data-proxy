package provider

import (
	"context"
	"maps"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/provider"
	"github.com/hashicorp/terraform-plugin-framework/providerserver"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/hashicorp/terraform-plugin-go/tfprotov6"
	"github.com/hashicorp/terraform-plugin-go/tftypes"

	"github.com/prefeitura-rio/data-proxy/terraform/internal/client"
)

func TestMetadata(t *testing.T) {
	var resp provider.MetadataResponse

	New("1.2.3")().Metadata(context.Background(), provider.MetadataRequest{}, &resp)

	if resp.TypeName != "dataproxy" {
		t.Errorf("TypeName = %q, want %q", resp.TypeName, "dataproxy")
	}

	if resp.Version != "1.2.3" {
		t.Errorf("Version = %q, want %q", resp.Version, "1.2.3")
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

	if len(resp.ResourceSchemas) != 0 {
		t.Errorf("got %d resources, want none", len(resp.ResourceSchemas))
	}

	permissions, ok := resp.DataSourceSchemas["dataproxy_permissions"]
	if len(resp.DataSourceSchemas) != 1 || !ok {
		t.Fatalf("data sources = %v, want only dataproxy_permissions", resp.DataSourceSchemas)
	}

	if attributes := permissions.Block.Attributes; len(attributes) != 1 || attributes[0].Name != "permissions" || !attributes[0].Computed {
		t.Errorf("attributes of dataproxy_permissions = %v, want only the computed permissions", attributes)
	}
}

// model builds a provider block. A nil string leaves the attribute out, and nil scopes leave the list out.
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

// ptr returns a pointer to the value.
func ptr(value string) *string {
	return &value
}

// diagnostic is the part of a diagnostic that a test compares.
type diagnostic struct {
	path    string
	summary string
	detail  string
}

// diagnosticsOf keeps the path, the summary and the detail of each diagnostic.
func diagnosticsOf(diagnostics diag.Diagnostics) []diagnostic {
	var got []diagnostic

	for _, d := range diagnostics {
		item := diagnostic{summary: d.Summary(), detail: d.Detail()}

		if withPath, ok := d.(diag.DiagnosticWithPath); ok {
			item.path = withPath.Path().String()
		}

		got = append(got, item)
	}

	return got
}

// fullEnv is an environment that sets every attribute of the provider.
var fullEnv = map[string]string{
	EnvEndpoint:     "https://env.example.com",
	EnvTokenURL:     "https://id.env.example.com/token",
	EnvClientID:     "env-client",
	EnvClientSecret: "env-secret",
	EnvScopes:       "openid, dataproxy\tprofile",
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
					Scopes:       []string{"openid", "dataproxy", "profile"},
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
					Scopes:       []string{"openid", "dataproxy", "profile"},
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
				{path: "endpoint", summary: "Missing endpoint", detail: "Set endpoint in the provider block or in the DATAPROXY_ENDPOINT environment variable."},
				{path: "token_url", summary: "Missing token_url", detail: "Set token_url in the provider block or in the DATAPROXY_TOKEN_URL environment variable."},
				{path: "client_id", summary: "Missing client_id", detail: "Set client_id in the provider block or in the DATAPROXY_CLIENT_ID environment variable."},
				{path: "client_secret", summary: "Missing client_secret", detail: "Set client_secret in the provider block or in the DATAPROXY_CLIENT_SECRET environment variable."},
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
				{path: "client_secret", summary: "Missing client_secret", detail: "Set client_secret in the provider block or in the DATAPROXY_CLIENT_SECRET environment variable."},
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
				{path: "endpoint", summary: "Unknown endpoint", detail: "The provider can't start with an unknown endpoint. Give a known value, or leave it out and set DATAPROXY_ENDPOINT."},
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
				{path: "scopes", summary: "Unknown scopes", detail: "The provider can't start with unknown scopes. Give known values, or leave them out and set DATAPROXY_SCOPES."},
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			getenv := func(name string) string { return tt.env[name] }

			got, diagnostics := readSettings(context.Background(), tt.model, getenv)

			if !reflect.DeepEqual(got, tt.want) {
				t.Errorf("settings = %+v, want %+v", got, tt.want)
			}

			if gotDiags := diagnosticsOf(diagnostics); !reflect.DeepEqual(gotDiags, tt.diags) {
				t.Errorf("diagnostics = %+v, want %+v", gotDiags, tt.diags)
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

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := splitScopes(tt.value); !reflect.DeepEqual(got, tt.want) {
				t.Errorf("splitScopes(%q) = %q, want %q", tt.value, got, tt.want)
			}
		})
	}
}

// configure runs Configure on a provider block with the given attributes. The other attributes are null.
func configure(t *testing.T, attributes map[string]tftypes.Value) provider.ConfigureResponse {
	t.Helper()

	ctx := context.Background()
	p := New("test")()

	var schemaResp provider.SchemaResponse

	p.Schema(ctx, provider.SchemaRequest{}, &schemaResp)

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

	p.Configure(ctx, req, &resp)

	return resp
}

// apiRequest is what the fake API saw of one request.
type apiRequest struct {
	method         string
	uri            string
	acceptProfile  string
	contentProfile string
	authorization  string
}

// fakeAPI holds the addresses of a fake identity provider and a fake PostgREST API, and the requests that the API saw.
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
					{path: "client_secret", summary: "Missing client_secret", detail: "Set client_secret in the provider block or in the DATAPROXY_CLIENT_SECRET environment variable."},
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

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tokenStatus := tt.tokenStatus
			if tokenStatus == 0 {
				tokenStatus = http.StatusOK
			}

			api := newFakeAPI(t, tokenStatus, tt.status, tt.body)

			env := map[string]string{}
			attributes := map[string]tftypes.Value{
				"endpoint":      text(api.endpoint),
				"token_url":     text(api.tokenURL),
				"client_id":     text("terraform"),
				"client_secret": text("secret-123"),
				"scopes":        tftypes.NewValue(tftypes.List{ElementType: tftypes.String}, []tftypes.Value{text("openid")}),
			}

			if tt.env {
				attributes = map[string]tftypes.Value{}
				env = map[string]string{
					EnvEndpoint:     api.endpoint,
					EnvTokenURL:     api.tokenURL,
					EnvClientID:     "env-client",
					EnvClientSecret: "env-secret",
					EnvScopes:       "openid profile",
				}
			}

			if tt.change != nil {
				tt.change(attributes)
			}

			for _, name := range []string{EnvEndpoint, EnvTokenURL, EnvClientID, EnvClientSecret, EnvScopes} {
				t.Setenv(name, env[name])
			}

			resp := configure(t, attributes)

			var want []diagnostic
			if tt.diags != nil {
				want = tt.diags(api)
			}

			if got := diagnosticsOf(resp.Diagnostics); !reflect.DeepEqual(got, want) {
				t.Fatalf("diagnostics = %+v, want %+v", got, want)
			}

			if len(*api.requests) != tt.requests {
				t.Fatalf("the API got %d requests, want %d", len(*api.requests), tt.requests)
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
