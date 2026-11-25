package provider

import (
	"context"
	"data-proxy/internal/client"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/datasource/schema"
)

// lookup is a data source that reads one object of the API into the model T. The name of the data source, its
// attributes and its read function make each one.
type lookup[T any] struct {
	client      *client.Client
	suffix      string
	description string
	attributes  map[string]schema.Attribute
	// permission is the permission of rbac.toml that the read needs, for the error text of a refused request.
	permission string
	read       func(ctx context.Context, apiClient *client.Client, model *T) error
	validators []datasource.ConfigValidator
}

var (
	_ datasource.DataSource                     = (*lookup[struct{}])(nil)
	_ datasource.DataSourceWithConfigure        = (*lookup[struct{}])(nil)
	_ datasource.DataSourceWithConfigValidators = (*lookup[struct{}])(nil)
)

// Metadata names the data source with the type name of the provider and its suffix.
func (l *lookup[T]) Metadata(_ context.Context, req datasource.MetadataRequest, resp *datasource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_" + l.suffix
}

// Schema declares the attributes of the data source.
func (l *lookup[T]) Schema(_ context.Context, _ datasource.SchemaRequest, resp *datasource.SchemaResponse) {
	resp.Schema = schema.Schema{Description: l.description, Attributes: l.attributes}
}

// ConfigValidators returns the validators that compare several attributes.
func (l *lookup[T]) ConfigValidators(context.Context) []datasource.ConfigValidator {
	return l.validators
}

// Configure takes the client that the provider built.
func (l *lookup[T]) Configure(_ context.Context, req datasource.ConfigureRequest, resp *datasource.ConfigureResponse) {
	l.client = providerClient(req.ProviderData, &resp.Diagnostics)
}

// Read fills the model from the API and stores it in the state.
func (l *lookup[T]) Read(ctx context.Context, req datasource.ReadRequest, resp *datasource.ReadResponse) {
	if l.client == nil {
		resp.Diagnostics.AddError(
			"Unconfigured provider",
			"The data source has no client, because the provider isn't configured.",
		)

		return
	}

	var model T

	resp.Diagnostics.Append(req.Config.Get(ctx, &model)...)

	if resp.Diagnostics.HasError() {
		return
	}

	err := l.read(ctx, l.client, &model)
	if err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't read "+l.suffix, l.permission, err)

		return
	}

	resp.Diagnostics.Append(resp.State.Set(ctx, model)...)
}
