package provider

import (
	"context"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/datasource/schema"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"terraform-provider-data-proxy/internal/client"
)

var (
	_ datasource.DataSource              = (*permissionsDataSource)(nil)
	_ datasource.DataSourceWithConfigure = (*permissionsDataSource)(nil)
)

// permissionsDataSource is the data_proxy_permissions data source. It reads the permissions of the token.
type permissionsDataSource struct {
	client *client.Client
}

// permissionsModel maps the data_proxy_permissions data source.
type permissionsModel struct {
	Permissions types.Set `tfsdk:"permissions"`
}

// newPermissionsDataSource returns the data_proxy_permissions data source.
func newPermissionsDataSource() datasource.DataSource {
	return &permissionsDataSource{}
}

// Metadata names the data source with the type name of the provider and the suffix _permissions.
func (d *permissionsDataSource) Metadata(_ context.Context, req datasource.MetadataRequest, resp *datasource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_permissions"
}

// Schema declares the computed set of permissions of the token.
func (d *permissionsDataSource) Schema(_ context.Context, _ datasource.SchemaRequest, resp *datasource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "Reads the permissions of the token of the provider, from GET /rpc/permissions.",
		Attributes: map[string]schema.Attribute{
			"permissions": schema.SetAttribute{
				Description: "The permissions of the token, the names of rbac.toml such as read_config and create_config.",
				ElementType: types.StringType,
				Computed:    true,
			},
		},
	}
}

// Configure takes the client that the provider built. It does nothing before the provider is configured.
func (d *permissionsDataSource) Configure(_ context.Context, req datasource.ConfigureRequest, resp *datasource.ConfigureResponse) {
	d.client = providerClient(req.ProviderData, &resp.Diagnostics)
}

// Read stores the permissions of the token in the state.
func (d *permissionsDataSource) Read(ctx context.Context, _ datasource.ReadRequest, resp *datasource.ReadResponse) {
	if d.client == nil {
		resp.Diagnostics.AddError("Unconfigured provider", "The data source has no client, because the provider isn't configured.")

		return
	}

	permissions, err := d.client.Permissions(ctx)
	if err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't read the permissions of the token", "", err)

		return
	}

	set, diagnostics := types.SetValueFrom(ctx, types.StringType, permissions)
	resp.Diagnostics.Append(diagnostics...)

	if resp.Diagnostics.HasError() {
		return
	}

	resp.Diagnostics.Append(resp.State.Set(ctx, permissionsModel{Permissions: set})...)
}
