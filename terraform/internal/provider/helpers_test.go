package provider

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"terraform-provider-data-proxy/internal/client"
)

func TestCompactJSON(t *testing.T) {
	tests := []struct {
		name string
		raw  json.RawMessage
		want string
	}{
		{name: "removes the space", raw: json.RawMessage(`{ "a": 1 }`), want: `{"a":1}`},
		{name: "an empty value", raw: nil, want: ""},
		{name: "a JSON null", raw: json.RawMessage("null"), want: ""},
		{name: "text that is not JSON", raw: json.RawMessage("{"), want: ""},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := string(compactJSON(tt.raw)); got != tt.want {
				t.Errorf("compactJSON(%s) = %q, want %q", tt.raw, got, tt.want)
			}
		})
	}
}

func TestJSONValue(t *testing.T) {
	tests := []struct {
		name    string
		raw     json.RawMessage
		current types.String
		want    types.String
	}{
		{name: "keeps the layout of the state for the same JSON", raw: json.RawMessage(`{"b":1,"a":2}`), current: types.StringValue(`{"a": 2, "b": 1}`), want: types.StringValue(`{"a": 2, "b": 1}`)},
		{name: "takes the new JSON when the value differs", raw: json.RawMessage(`{"a":3}`), current: types.StringValue(`{"a": 2}`), want: types.StringValue(`{"a":3}`)},
		{name: "takes the compact JSON without a state", raw: json.RawMessage(`{ "a": 1 }`), current: types.StringNull(), want: types.StringValue(`{"a":1}`)},
		{name: "a JSON null is null", raw: json.RawMessage("null"), current: types.StringValue(`{"a":1}`), want: types.StringNull()},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := jsonValue(tt.raw, tt.current); !got.Equal(tt.want) {
				t.Errorf("jsonValue = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestJSONRaw(t *testing.T) {
	if got := jsonRaw(types.StringNull()); got != nil {
		t.Errorf("jsonRaw(null) = %s, want nil", got)
	}

	if got := jsonRaw(types.StringUnknown()); got != nil {
		t.Errorf("jsonRaw(unknown) = %s, want nil", got)
	}

	if got := jsonRaw(types.StringValue(`{"a":1}`)); string(got) != `{"a":1}` {
		t.Errorf("jsonRaw = %s, want the text", got)
	}
}

func TestOptionalConversions(t *testing.T) {
	if got := stringValue(nil); !got.IsNull() {
		t.Errorf("stringValue(nil) = %v, want null", got)
	}

	if got := stringValue(ptr("x")); got.ValueString() != "x" {
		t.Errorf("stringValue = %v, want x", got)
	}

	if stringPointer(types.StringNull()) != nil || stringPointer(types.StringUnknown()) != nil {
		t.Error("stringPointer of null or unknown must be nil")
	}

	if got := stringPointer(types.StringValue("x")); got == nil || *got != "x" {
		t.Errorf("stringPointer = %v, want x", got)
	}

	if got := intValue(nil); !got.IsNull() {
		t.Errorf("intValue(nil) = %v, want null", got)
	}

	if got := intValue(ptr(3)); got.ValueInt64() != 3 {
		t.Errorf("intValue = %v, want 3", got)
	}

	if intPointer(types.Int64Null()) != nil || intPointer(types.Int64Unknown()) != nil {
		t.Error("intPointer of null or unknown must be nil")
	}

	if got := intPointer(types.Int64Value(4)); got == nil || *got != 4 {
		t.Errorf("intPointer = %v, want 4", got)
	}
}

func TestJSONValidators(t *testing.T) {
	tests := []struct {
		name      string
		validator validator.String
		value     types.String
		wantError bool
	}{
		{name: "an object for an object", validator: jsonObject(), value: types.StringValue(`{"a":1}`)},
		{name: "an empty object", validator: jsonObject(), value: types.StringValue(`{}`), wantError: true},
		{name: "an array for an object", validator: jsonObject(), value: types.StringValue(`[1]`), wantError: true},
		{name: "a scalar for an object", validator: jsonObject(), value: types.StringValue(`1`), wantError: true},
		{name: "text that is not JSON", validator: jsonObject(), value: types.StringValue(`{`), wantError: true},
		{name: "an array for an array", validator: jsonArray(), value: types.StringValue(`[{"a":1}]`)},
		{name: "an object for an array", validator: jsonArray(), value: types.StringValue(`{"a":1}`), wantError: true},
		{name: "a null value", validator: jsonObject(), value: types.StringNull()},
		{name: "an unknown value", validator: jsonArray(), value: types.StringUnknown()},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			resp := validator.StringResponse{}

			tt.validator.ValidateString(context.Background(), validator.StringRequest{Path: path.Root("settings"), ConfigValue: tt.value}, &resp)

			if resp.Diagnostics.HasError() != tt.wantError {
				t.Errorf("errors = %v, want an error: %t", resp.Diagnostics, tt.wantError)
			}
		})
	}

	if got := jsonObject().MarkdownDescription(context.Background()); !strings.Contains(got, "JSON object") {
		t.Errorf("description = %q, want it to name a JSON object", got)
	}
}

func TestProviderClient(t *testing.T) {
	var diagnostics diag.Diagnostics

	if got := providerClient(nil, &diagnostics); got != nil || diagnostics.HasError() {
		t.Errorf("providerClient(nil) = %v, %v, want nil and no error", got, diagnostics)
	}

	apiClient := &client.Client{}

	if got := providerClient(apiClient, &diagnostics); got != apiClient || diagnostics.HasError() {
		t.Errorf("providerClient = %v, %v, want the client and no error", got, diagnostics)
	}

	if got := providerClient("not a client", &diagnostics); got != nil || !diagnostics.HasError() {
		t.Errorf("providerClient(string) = %v, %v, want nil and an error", got, diagnostics)
	}
}

func TestSchemasAreValid(t *testing.T) {
	ctx := context.Background()
	p := New("test")()

	for _, newResource := range p.Resources(ctx) {
		res := newResource()

		if diagnostics := resourceSchema(t, res).ValidateImplementation(ctx); diagnostics.HasError() {
			t.Errorf("%T schema: %v", res, diagnostics)
		}
	}

	for _, newSource := range p.DataSources(ctx) {
		source := newSource()

		if diagnostics := dataSourceSchema(t, source).ValidateImplementation(ctx); diagnostics.HasError() {
			t.Errorf("%T schema: %v", source, diagnostics)
		}
	}
}
