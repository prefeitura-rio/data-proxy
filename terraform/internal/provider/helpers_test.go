package provider

import (
	"context"
	"encoding/json"
	"fmt"
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

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			if got := string(compactJSON(testCase.raw)); got != testCase.want {
				t.Errorf("compactJSON(%s) = %q, want %q", testCase.raw, got, testCase.want)
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

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			if got := jsonValue(testCase.raw, testCase.current); !got.Equal(testCase.want) {
				t.Errorf("jsonValue = %v, want %v", got, testCase.want)
			}
		})
	}
}

// TestJSONRaw checks that a null or unknown text gives no JSON, and a known text gives itself.
func TestJSONRaw(t *testing.T) {
	tests := []struct {
		name  string
		input types.String
		want  string
	}{
		{name: "null", input: types.StringNull()},
		{name: "unknown", input: types.StringUnknown()},
		{name: "a value", input: types.StringValue(`{"a":1}`), want: `{"a":1}`},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			got := jsonRaw(testCase.input)

			if (got == nil) != (testCase.want == "") || string(got) != testCase.want {
				t.Errorf("jsonRaw() = %s, want %q", got, testCase.want)
			}
		})
	}
}

// TestOptionalConversions checks the pointer and value conversions of the optional text and number attributes.
func TestOptionalConversions(t *testing.T) {
	tests := []struct {
		name string
		run  func() (got, want string)
	}{
		{name: "a nil text is null", run: func() (string, string) { return stringValue(nil).String(), types.StringNull().String() }},
		{name: "a text is a value", run: func() (string, string) { return stringValue(ptr("x")).ValueString(), "x" }},
		{name: "a null text has no pointer", run: func() (string, string) { return fmt.Sprint(stringPointer(types.StringNull()) == nil), "true" }},
		{name: "an unknown text has no pointer", run: func() (string, string) { return fmt.Sprint(stringPointer(types.StringUnknown()) == nil), "true" }},
		{name: "a text has a pointer", run: func() (string, string) { return *stringPointer(types.StringValue("x")), "x" }},
		{name: "a nil number is null", run: func() (string, string) { return intValue(nil).String(), types.Int64Null().String() }},
		{name: "a number is a value", run: func() (string, string) { return fmt.Sprint(intValue(ptr(3)).ValueInt64()), "3" }},
		{name: "a null number has no pointer", run: func() (string, string) { return fmt.Sprint(intPointer(types.Int64Null()) == nil), "true" }},
		{name: "an unknown number has no pointer", run: func() (string, string) { return fmt.Sprint(intPointer(types.Int64Unknown()) == nil), "true" }},
		{name: "a number has a pointer", run: func() (string, string) { return fmt.Sprint(*intPointer(types.Int64Value(4))), "4" }},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			if got, want := testCase.run(); got != want {
				t.Errorf("got %q, want %q", got, want)
			}
		})
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

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			resp := validator.StringResponse{}

			testCase.validator.ValidateString(context.Background(), validator.StringRequest{Path: path.Root("settings"), ConfigValue: testCase.value}, &resp)

			if resp.Diagnostics.HasError() != testCase.wantError {
				t.Errorf("errors = %v, want an error: %t", resp.Diagnostics, testCase.wantError)
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

	c := &client.Client{}

	if got := providerClient(c, &diagnostics); got != c || diagnostics.HasError() {
		t.Errorf("providerClient = %v, %v, want the client and no error", got, diagnostics)
	}

	if got := providerClient("not a client", &diagnostics); got != nil || !diagnostics.HasError() {
		t.Errorf("providerClient(string) = %v, %v, want nil and an error", got, diagnostics)
	}
}

func TestSchemasAreValid(t *testing.T) {
	ctx := context.Background()
	dataProxy := New("test")()

	for _, newResource := range dataProxy.Resources(ctx) {
		instance := newResource()

		if diagnostics := resourceSchema(t, instance).ValidateImplementation(ctx); diagnostics.HasError() {
			t.Errorf("%T schema: %v", instance, diagnostics)
		}
	}

	for _, newSource := range dataProxy.DataSources(ctx) {
		source := newSource()

		if diagnostics := dataSourceSchema(t, source).ValidateImplementation(ctx); diagnostics.HasError() {
			t.Errorf("%T schema: %v", source, diagnostics)
		}
	}
}
