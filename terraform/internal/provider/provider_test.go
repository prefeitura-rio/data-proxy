package provider

import (
	"context"
	"maps"
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

	if len(resp.ResourceSchemas) != 0 || len(resp.DataSourceSchemas) != 0 {
		t.Errorf("got %d resources and %d data sources, want none", len(resp.ResourceSchemas), len(resp.DataSourceSchemas))
	}
}

// model builds a provider block. A nil string pointer leaves the attribute out, and nil scopes leave the list out.
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

func ptr(value string) *string {
	return &value
}

// diagnostic is the part of a diagnostic that a test compares.
type diagnostic struct {
	path    string
	summary string
	detail  string
}

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
		credentials: client.ClientCredentials{
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
		id    string
		model providerModel
		env   map[string]string
		want  settings
		diags []diagnostic
	}{
		{
			id:    "every attribute in the block",
			model: hcl,
			want:  hclSettings,
		},
		{
			id:    "every attribute from the environment",
			model: model(nil, nil, nil, nil, nil),
			env:   fullEnv,
			want: settings{
				endpoint: "https://env.example.com",
				credentials: client.ClientCredentials{
					TokenURL:     "https://id.env.example.com/token",
					ClientID:     "env-client",
					ClientSecret: "env-secret",
					Scopes:       []string{"openid", "dataproxy", "profile"},
				},
			},
		},
		{
			id:    "the block takes precedence over the environment",
			model: hcl,
			env:   fullEnv,
			want:  hclSettings,
		},
		{
			id:    "the environment fills only what the block leaves out",
			model: model(ptr("https://hcl.example.com"), nil, ptr("hcl-client"), nil, nil),
			env:   fullEnv,
			want: settings{
				endpoint: "https://hcl.example.com",
				credentials: client.ClientCredentials{
					TokenURL:     "https://id.env.example.com/token",
					ClientID:     "hcl-client",
					ClientSecret: "env-secret",
					Scopes:       []string{"openid", "dataproxy", "profile"},
				},
			},
		},
		{
			id:    "an empty scope list in the block takes precedence over the environment",
			model: model(ptr("https://hcl.example.com"), ptr("https://id.hcl.example.com/token"), ptr("hcl-client"), ptr("hcl-secret"), []string{}),
			env:   fullEnv,
			want: settings{
				endpoint: "https://hcl.example.com",
				credentials: client.ClientCredentials{
					TokenURL:     "https://id.hcl.example.com/token",
					ClientID:     "hcl-client",
					ClientSecret: "hcl-secret",
					Scopes:       []string{},
				},
			},
		},
		{
			id:    "no scopes anywhere",
			model: model(ptr("https://hcl.example.com"), ptr("https://id.hcl.example.com/token"), ptr("hcl-client"), ptr("hcl-secret"), nil),
			want: settings{
				endpoint: "https://hcl.example.com",
				credentials: client.ClientCredentials{
					TokenURL:     "https://id.hcl.example.com/token",
					ClientID:     "hcl-client",
					ClientSecret: "hcl-secret",
					Scopes:       []string{},
				},
			},
		},
		{
			id:    "every required value missing",
			model: model(nil, nil, nil, nil, nil),
			env:   map[string]string{EnvScopes: "openid"},
			want:  settings{credentials: client.ClientCredentials{Scopes: []string{"openid"}}},
			diags: []diagnostic{
				{path: "endpoint", summary: "Missing endpoint", detail: "Set endpoint in the provider block or in the DATAPROXY_ENDPOINT environment variable."},
				{path: "token_url", summary: "Missing token_url", detail: "Set token_url in the provider block or in the DATAPROXY_TOKEN_URL environment variable."},
				{path: "client_id", summary: "Missing client_id", detail: "Set client_id in the provider block or in the DATAPROXY_CLIENT_ID environment variable."},
				{path: "client_secret", summary: "Missing client_secret", detail: "Set client_secret in the provider block or in the DATAPROXY_CLIENT_SECRET environment variable."},
			},
		},
		{
			id:    "an empty string in the block hides the environment",
			model: model(ptr("https://hcl.example.com"), ptr("https://id.hcl.example.com/token"), ptr("hcl-client"), ptr(""), []string{"hcl"}),
			env:   fullEnv,
			want: settings{
				endpoint: "https://hcl.example.com",
				credentials: client.ClientCredentials{
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
			id:    "an unknown endpoint",
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
			id:    "unknown scopes",
			model: unknownScopes,
			env:   fullEnv,
			want: settings{
				endpoint: hclSettings.endpoint,
				credentials: client.ClientCredentials{
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
		t.Run(tt.id, func(t *testing.T) {
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
		id    string
		value string
		want  []string
	}{
		{id: "empty", value: "", want: []string{}},
		{id: "one scope", value: "openid", want: []string{"openid"}},
		{id: "commas", value: "openid,profile", want: []string{"openid", "profile"}},
		{id: "spaces", value: "openid profile", want: []string{"openid", "profile"}},
		{id: "commas, spaces and empty items", value: " openid, ,profile\n email ", want: []string{"openid", "profile", "email"}},
	}

	for _, tt := range tests {
		t.Run(tt.id, func(t *testing.T) {
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

func TestConfigure(t *testing.T) {
	text := func(value string) tftypes.Value { return tftypes.NewValue(tftypes.String, value) }

	valid := map[string]tftypes.Value{
		"endpoint":      text("https://data-proxy.example.com"),
		"token_url":     text("https://id.example.com/realms/example/protocol/openid-connect/token"),
		"client_id":     text("terraform"),
		"client_secret": text("secret-123"),
		"scopes":        tftypes.NewValue(tftypes.List{ElementType: tftypes.String}, []tftypes.Value{text("openid")}),
	}

	with := func(name string, value tftypes.Value) map[string]tftypes.Value {
		changed := maps.Clone(valid)
		changed[name] = value

		return changed
	}

	tests := []struct {
		id         string
		attributes map[string]tftypes.Value
		env        map[string]string
		diags      []diagnostic
	}{
		{
			id:         "a full provider block",
			attributes: valid,
		},
		{
			id:         "an empty provider block and the environment",
			attributes: map[string]tftypes.Value{},
			env:        fullEnv,
		},
		{
			id:         "a missing client secret",
			attributes: with("client_secret", tftypes.NewValue(tftypes.String, nil)),
			diags: []diagnostic{
				{path: "client_secret", summary: "Missing client_secret", detail: "Set client_secret in the provider block or in the DATAPROXY_CLIENT_SECRET environment variable."},
			},
		},
		{
			id:         "an endpoint without a scheme",
			attributes: with("endpoint", text("data-proxy.example.com")),
			diags: []diagnostic{
				{path: "endpoint", summary: "Invalid Data Proxy endpoint", detail: `endpoint "data-proxy.example.com": the scheme must be http or https`},
			},
		},
		{
			id:         "a token URL without a scheme",
			attributes: with("token_url", text("id.example.com/token")),
			diags: []diagnostic{
				{summary: "Invalid Data Proxy credentials", detail: `token URL "id.example.com/token": the scheme must be http or https`},
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.id, func(t *testing.T) {
			for _, name := range []string{EnvEndpoint, EnvTokenURL, EnvClientID, EnvClientSecret, EnvScopes} {
				t.Setenv(name, tt.env[name])
			}

			resp := configure(t, tt.attributes)

			if got := diagnosticsOf(resp.Diagnostics); !reflect.DeepEqual(got, tt.diags) {
				t.Fatalf("diagnostics = %+v, want %+v", got, tt.diags)
			}

			if tt.diags != nil {
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
