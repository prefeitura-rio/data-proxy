// Package provider implements the dataproxy Terraform provider.
package provider

import (
	"context"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/provider"
	"github.com/hashicorp/terraform-plugin-framework/provider/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

// Address is the registry address that Terraform uses to find the provider.
const Address = "registry.terraform.io/prefeitura-rio/dataproxy"

// TypeName is the prefix of the resource and data source names of the provider.
const TypeName = "dataproxy"

var _ provider.Provider = (*dataProxyProvider)(nil)

// dataProxyProvider is the provider. It has no resources and no data sources yet.
type dataProxyProvider struct {
	version string
}

// providerModel maps the provider block of a Terraform configuration.
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

func (p *dataProxyProvider) Metadata(_ context.Context, _ provider.MetadataRequest, resp *provider.MetadataResponse) {
	resp.TypeName = TypeName
	resp.Version = p.version
}

func (p *dataProxyProvider) Schema(_ context.Context, _ provider.SchemaRequest, resp *provider.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "Manages the configuration of Data Proxy through its PostgREST API.",
		Attributes: map[string]schema.Attribute{
			"endpoint": schema.StringAttribute{
				Description: "Base URL of the Data Proxy API.",
				Optional:    true,
			},
			"token_url": schema.StringAttribute{
				Description: "URL of the token endpoint of the identity provider.",
				Optional:    true,
			},
			"client_id": schema.StringAttribute{
				Description: "Client ID for the client credentials grant.",
				Optional:    true,
			},
			"client_secret": schema.StringAttribute{
				Description: "Client secret for the client credentials grant.",
				Optional:    true,
				Sensitive:   true,
			},
			"scopes": schema.ListAttribute{
				Description: "Scopes to request with the token.",
				ElementType: types.StringType,
				Optional:    true,
			},
		},
	}
}

// Configure reads the provider block. The values aren't used yet.
func (p *dataProxyProvider) Configure(ctx context.Context, req provider.ConfigureRequest, resp *provider.ConfigureResponse) {
	var config providerModel

	resp.Diagnostics.Append(req.Config.Get(ctx, &config)...)
}

func (p *dataProxyProvider) Resources(_ context.Context) []func() resource.Resource {
	return nil
}

func (p *dataProxyProvider) DataSources(_ context.Context) []func() datasource.DataSource {
	return nil
}
