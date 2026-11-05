package provider

import (
	"context"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/provider"
	"github.com/hashicorp/terraform-plugin-framework/providerserver"
	"github.com/hashicorp/terraform-plugin-go/tfprotov6"
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
