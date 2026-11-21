package provider

import (
	"context"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/datasource/schema"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-go/tftypes"
)

// noValidator is a ConfigValidator that checks nothing, for a test of the validators that a lookup returns.
type noValidator struct{}

// Description describes the validator.
func (noValidator) Description(context.Context) string { return "no check" }

// MarkdownDescription describes the validator.
func (noValidator) MarkdownDescription(context.Context) string { return "no check" }

// ValidateDataSource checks nothing.
func (noValidator) ValidateDataSource(context.Context, datasource.ValidateConfigRequest, *datasource.ValidateConfigResponse) {
}

// TestLookupMetadata checks that the type name joins the name of the provider and the suffix.
func TestLookupMetadata(t *testing.T) {
	tests := []struct {
		name     string
		provider string
		suffix   string
		want     string
	}{
		{name: "a project", provider: "data_proxy", suffix: "project", want: "data_proxy_project"},
		{name: "a table", provider: "data_proxy", suffix: "table", want: "data_proxy_table"},
		{name: "another provider name", provider: "dp", suffix: "schema_health", want: "dp_schema_health"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			source := &lookup[struct{}]{suffix: tt.suffix}
			var resp datasource.MetadataResponse

			source.Metadata(context.Background(), datasource.MetadataRequest{ProviderTypeName: tt.provider}, &resp)

			if resp.TypeName != tt.want {
				t.Errorf("TypeName = %q, want %q", resp.TypeName, tt.want)
			}
		})
	}
}

// TestLookupConfigValidators checks that the lookup returns the validators that it holds, and none when it holds none.
func TestLookupConfigValidators(t *testing.T) {
	tests := []struct {
		name       string
		validators []datasource.ConfigValidator
		want       int
	}{
		{name: "none", want: 0},
		{name: "one", validators: []datasource.ConfigValidator{noValidator{}}, want: 1},
		{name: "two", validators: []datasource.ConfigValidator{noValidator{}, noValidator{}}, want: 2},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			source := &lookup[struct{}]{validators: tt.validators}

			if got := len(source.ConfigValidators(context.Background())); got != tt.want {
				t.Errorf("len(ConfigValidators()) = %d, want %d", got, tt.want)
			}
		})
	}
}

// TestLookupReadWithoutAClient checks that a read before the provider is configured adds the error and sets no state.
func TestLookupReadWithoutAClient(t *testing.T) {
	tests := []struct {
		name   string
		suffix string
	}{
		{name: "a project", suffix: "project"},
		{name: "a table", suffix: "table"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			source := &lookup[struct{}]{suffix: tt.suffix, attributes: map[string]schema.Attribute{}}
			s := dataSourceSchema(t, source)
			raw := tftypes.NewValue(s.Type().TerraformType(context.Background()), nil)
			resp := datasource.ReadResponse{State: tfsdk.State{Schema: s, Raw: raw}}

			source.Read(context.Background(), datasource.ReadRequest{Config: tfsdk.Config{Schema: s, Raw: raw}}, &resp)

			if got := summaries(diagnosticsOf(resp.Diagnostics)); len(got) != 1 || got[0] != "Unconfigured provider" {
				t.Errorf("diagnostics = %v, want the error of a missing client", got)
			}
		})
	}
}
