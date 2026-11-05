// Package provider implements the data_proxy Terraform provider.
package provider

import (
	"context"
	"fmt"
	"os"
	"strings"
	"unicode"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/provider"
	"github.com/hashicorp/terraform-plugin-framework/provider/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"terraform-provider-data-proxy/internal/client"
)

// TypeName is the prefix of the resource and data source names.
const TypeName = "data_proxy"

// The environment variables for the attributes of the provider block. An attribute in the block wins.
const (
	EnvEndpoint     = "DATA_PROXY_ENDPOINT"
	EnvTokenURL     = "DATA_PROXY_TOKEN_URL"
	EnvClientID     = "DATA_PROXY_CLIENT_ID"
	EnvClientSecret = "DATA_PROXY_CLIENT_SECRET"
	// EnvScopes holds scopes separated by commas or spaces.
	EnvScopes = "DATA_PROXY_SCOPES"
)

var _ provider.Provider = (*dataProxyProvider)(nil)

// dataProxyProvider is the provider. Configure gives its resources and data sources a *client.Client.
type dataProxyProvider struct {
	version string
}

// settings are the values that the provider needs, after the environment fills the attributes that the block
// leaves out.
type settings struct {
	endpoint    string
	credentials client.Credentials
}

// providerModel maps the provider block.
type providerModel struct {
	Endpoint     types.String `tfsdk:"endpoint"`
	TokenURL     types.String `tfsdk:"token_url"`
	ClientID     types.String `tfsdk:"client_id"`
	ClientSecret types.String `tfsdk:"client_secret"`
	Scopes       types.List   `tfsdk:"scopes"`
}

// New returns a function that builds the provider with the given version.
func New(version string) func() provider.Provider {
	return func() provider.Provider {
		return &dataProxyProvider{version: version}
	}
}

// Metadata sets the type name and the version of the provider.
func (p *dataProxyProvider) Metadata(_ context.Context, _ provider.MetadataRequest, resp *provider.MetadataResponse) {
	resp.TypeName = TypeName
	resp.Version = p.version
}

// Schema declares the attributes of the provider block. Each one can come from an environment variable.
func (p *dataProxyProvider) Schema(_ context.Context, _ provider.SchemaRequest, resp *provider.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "Manages the configuration of Data Proxy through its PostgREST API.",
		Attributes: map[string]schema.Attribute{
			"endpoint": schema.StringAttribute{
				Description: "Base URL of the Data Proxy API. Required. Defaults to the " + EnvEndpoint + " environment variable.",
				Optional:    true,
			},
			"token_url": schema.StringAttribute{
				Description: "URL of the token endpoint of the identity provider. Required. Defaults to the " + EnvTokenURL + " environment variable.",
				Optional:    true,
			},
			"client_id": schema.StringAttribute{
				Description: "Client ID for the client credentials grant. Required. Defaults to the " + EnvClientID + " environment variable.",
				Optional:    true,
			},
			"client_secret": schema.StringAttribute{
				Description: "Client secret for the client credentials grant. Required. Defaults to the " + EnvClientSecret + " environment variable.",
				Optional:    true,
				Sensitive:   true,
			},
			"scopes": schema.ListAttribute{
				Description: "Scopes to request with the token. Defaults to the " + EnvScopes + " environment variable, a list separated by commas or spaces.",
				ElementType: types.StringType,
				Optional:    true,
			},
		},
	}
}

// Configure gives the resources and the data sources a client, and fails when the token doesn't work or lacks
// read_config.
func (p *dataProxyProvider) Configure(ctx context.Context, req provider.ConfigureRequest, resp *provider.ConfigureResponse) {
	var config providerModel

	resp.Diagnostics.Append(req.Config.Get(ctx, &config)...)

	if resp.Diagnostics.HasError() {
		return
	}

	values, diagnostics := readSettings(ctx, config, os.Getenv)
	resp.Diagnostics.Append(diagnostics...)

	if resp.Diagnostics.HasError() {
		return
	}

	tokens, err := client.NewCredentialsSource(values.credentials, nil)
	if err != nil {
		resp.Diagnostics.AddError("Invalid Data Proxy credentials", err.Error())

		return
	}

	apiClient, err := client.New(values.endpoint, tokens, nil)
	if err != nil {
		resp.Diagnostics.AddAttributeError(path.Root("endpoint"), "Invalid Data Proxy endpoint", err.Error())

		return
	}

	permissions, err := apiClient.Permissions(ctx)
	if err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't check the Data Proxy token", "", err)

		return
	}

	resp.Diagnostics.Append(checkReadConfig(values.credentials.ClientID, permissions)...)

	if resp.Diagnostics.HasError() {
		return
	}

	resp.ResourceData = apiClient
	resp.DataSourceData = apiClient
}

// readSettings takes each attribute from the provider block, or from its environment variable when the block
// leaves it out. It reports an unknown value and each required value that is empty.
func readSettings(ctx context.Context, config providerModel, getenv func(string) string) (settings, diag.Diagnostics) {
	var diagnostics diag.Diagnostics

	text := func(attribute string, value types.String, env string) string {
		if value.IsUnknown() {
			diagnostics.AddAttributeError(path.Root(attribute), "Unknown "+attribute,
				fmt.Sprintf("The provider can't start with an unknown %s. Give a known value, or leave it out and set %s.", attribute, env))

			return ""
		}

		if value.IsNull() {
			return getenv(env)
		}

		return value.ValueString()
	}

	values := settings{
		endpoint: text("endpoint", config.Endpoint, EnvEndpoint),
		credentials: client.Credentials{
			TokenURL:     text("token_url", config.TokenURL, EnvTokenURL),
			ClientID:     text("client_id", config.ClientID, EnvClientID),
			ClientSecret: text("client_secret", config.ClientSecret, EnvClientSecret),
		},
	}

	switch {
	case config.Scopes.IsUnknown():
		diagnostics.AddAttributeError(path.Root("scopes"), "Unknown scopes",
			fmt.Sprintf("The provider can't start with unknown scopes. Give known values, or leave them out and set %s.", EnvScopes))
	case config.Scopes.IsNull():
		values.credentials.Scopes = splitScopes(getenv(EnvScopes))
	default:
		diagnostics.Append(config.Scopes.ElementsAs(ctx, &values.credentials.Scopes, false)...)
	}

	required := []struct {
		attribute string
		env       string
		value     string
		unknown   bool
	}{
		{attribute: "endpoint", env: EnvEndpoint, value: values.endpoint, unknown: config.Endpoint.IsUnknown()},
		{attribute: "token_url", env: EnvTokenURL, value: values.credentials.TokenURL, unknown: config.TokenURL.IsUnknown()},
		{attribute: "client_id", env: EnvClientID, value: values.credentials.ClientID, unknown: config.ClientID.IsUnknown()},
		{attribute: "client_secret", env: EnvClientSecret, value: values.credentials.ClientSecret, unknown: config.ClientSecret.IsUnknown()},
	}

	for _, setting := range required {
		if setting.value == "" && !setting.unknown {
			diagnostics.AddAttributeError(path.Root(setting.attribute), "Missing "+setting.attribute,
				fmt.Sprintf("Set %s in the provider block or in the %s environment variable.", setting.attribute, setting.env))
		}
	}

	return values, diagnostics
}

// splitScopes splits a list of scopes at commas and spaces.
func splitScopes(value string) []string {
	return strings.FieldsFunc(value, func(r rune) bool {
		return r == ',' || unicode.IsSpace(r)
	})
}

// Resources lists the resources of the provider. It has none yet.
func (p *dataProxyProvider) Resources(_ context.Context) []func() resource.Resource {
	return nil
}

// DataSources lists the data sources of the provider.
func (p *dataProxyProvider) DataSources(_ context.Context) []func() datasource.DataSource {
	return []func() datasource.DataSource{newPermissionsDataSource}
}
