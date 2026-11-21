package provider

import (
	"context"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

func TestSchemaProblem(t *testing.T) {
	tests := []struct {
		name    string
		project string
		short   string
		want    string
	}{
		{name: "a plain name", project: "sales", short: "orders"},
		{name: "a single underscore", project: "sales", short: "a_b_"},
		{name: "two underscores in a row", project: "sales", short: "a__b", want: "A schema name can't contain two underscores in a row."},
		{name: "exactly 56 bytes", project: "sales", short: strings.Repeat("a", 49)},
		{name: "57 bytes", project: "sales", short: strings.Repeat("a", 50), want: "The name sales__" + strings.Repeat("a", 50) + " has 57 bytes. The limit is 56."},
		{name: "default counts as a project name", project: "default", short: strings.Repeat("a", 47)},
		{name: "default with one byte too many", project: "default", short: strings.Repeat("a", 48), want: "The name default__" + strings.Repeat("a", 48) + " has 57 bytes. The limit is 56."},
		{name: "a multibyte letter counts in bytes", project: "sales", short: strings.Repeat("a", 48) + "é", want: "The name sales__" + strings.Repeat("a", 48) + "é has 57 bytes. The limit is 56."},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			if got := schemaProblem(testCase.project, testCase.short); got != testCase.want {
				t.Errorf("schemaProblem(%q, %q) = %q, want %q", testCase.project, testCase.short, got, testCase.want)
			}
		})
	}
}

func TestSchemaValidateConfig(t *testing.T) {
	tests := []struct {
		name    string
		project types.String
		short   types.String
		want    []string
	}{
		{name: "a valid name", project: types.StringValue("sales"), short: types.StringValue("orders")},
		{name: "no project means default", project: types.StringNull(), short: types.StringValue(strings.Repeat("a", 48)), want: []string{"Invalid schema name"}},
		{name: "two underscores", project: types.StringValue("sales"), short: types.StringValue("a__b"), want: []string{"Invalid schema name"}},
		{name: "an unknown short name", project: types.StringValue("sales"), short: types.StringUnknown()},
		{name: "an unknown project", project: types.StringUnknown(), short: types.StringValue("a__b")},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			testSchema := newTestSchema(t, nil)
			s := resourceSchema(t, testSchema)
			model := schemaPlan("")
			model.Project, model.ShortName = testCase.project, testCase.short
			config := tfsdk.Config{Schema: s, Raw: planOf(t, s, model).Raw}
			resp := resource.ValidateConfigResponse{}

			testSchema.(resource.ResourceWithValidateConfig).ValidateConfig(context.Background(), resource.ValidateConfigRequest{Config: config}, &resp)

			got := summaries(diagnosticsOf(resp.Diagnostics))
			if strings.Join(got, ",") != strings.Join(testCase.want, ",") {
				t.Errorf("diagnostics = %v, want %v", got, testCase.want)
			}
		})
	}
}

func TestReservedProjectsAreRefused(t *testing.T) {
	for _, name := range reservedProjects {
		if !projectName.MatchString(name) {
			t.Errorf("%q can't match the project pattern, so it doesn't need a place in the list", name)
		}
	}
}

func TestSchemaNamesBeforeAndAfterTheProject(t *testing.T) {
	tests := []struct {
		name     string
		physical string
		legacy   string
	}{
		{"a schema of the project default", "default__api_bcadastro", "api_bcadastro"},
		{"a schema of another project", "sales__orders", "sales_orders"},
		{"a name without the form", "plain", "plain"},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			if got := legacySchemaName(testCase.physical); got != testCase.legacy {
				t.Errorf("legacySchemaName(%q) = %q, want %q", testCase.physical, got, testCase.legacy)
			}
		})
	}

	if got := physicalSchemaName("", "hr"); got != "default__hr" {
		t.Errorf("physicalSchemaName with no project = %q, want default__hr", got)
	}

	if !renamedSchema("sales_orders", "sales__orders") || renamedSchema("sales__orders", "sales__orders") || renamedSchema("hr", "sales__orders") {
		t.Error("renamedSchema must be true only for a legacy name of the planned schema")
	}
}
