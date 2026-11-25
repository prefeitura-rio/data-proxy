package provider_test

import (
	"context"
	"data-proxy/internal/client"
	"data-proxy/internal/provider"
	"encoding/json"
	"strconv"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

func TestCompactJSON(t *testing.T) {
	t.Parallel()

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
			t.Parallel()

			if got := string(provider.CompactJSON(testCase.raw)); got != testCase.want {
				t.Errorf("compactJSON(%s) = %q, want %q", testCase.raw, got, testCase.want)
			}
		})
	}
}

func TestJSONValue(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name    string
		raw     json.RawMessage
		current types.String
		want    types.String
	}{
		{
			name:    "keeps the layout of the state for the same JSON",
			raw:     json.RawMessage(`{"b":1,"a":2}`),
			current: types.StringValue(`{"a": 2, "b": 1}`),
			want:    types.StringValue(`{"a": 2, "b": 1}`),
		},
		{
			name:    "takes the new JSON when the value differs",
			raw:     json.RawMessage(`{"a":3}`),
			current: types.StringValue(`{"a": 2}`),
			want:    types.StringValue(`{"a":3}`),
		},
		{
			name:    "takes the compact JSON without a state",
			raw:     json.RawMessage(`{ "a": 1 }`),
			current: types.StringNull(),
			want:    types.StringValue(`{"a":1}`),
		},
		{
			name:    "a JSON null is null",
			raw:     json.RawMessage("null"),
			current: types.StringValue(`{"a":1}`),
			want:    types.StringNull(),
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			if got := provider.JsonValue(testCase.raw, testCase.current); !got.Equal(testCase.want) {
				t.Errorf("jsonValue = %v, want %v", got, testCase.want)
			}
		})
	}
}

// TestJSONRaw checks that a null or unknown text gives no JSON, and a known text gives itself.
func TestJSONRaw(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name  string
		input types.String
		want  string
	}{
		{name: nullJSON, input: types.StringNull()},
		{name: "unknown", input: types.StringUnknown()},
		{name: "a value", input: types.StringValue(`{"a":1}`), want: `{"a":1}`},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			got := provider.JsonRaw(testCase.input)

			if (got == nil) != (testCase.want == "") || string(got) != testCase.want {
				t.Errorf("jsonRaw() = %s, want %q", got, testCase.want)
			}
		})
	}
}

// TestOptionalConversions checks the pointer and value conversions of the optional text and number attributes.
func TestOptionalConversions(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		run  func() (got, want string)
	}{
		{
			name: "a nil text is null",
			run:  func() (string, string) { return provider.StringValue(nil).String(), types.StringNull().String() },
		},
		{
			name: "a text is a value",
			run:  func() (string, string) { return provider.StringValue(new("x")).ValueString(), "x" },
		},
		{
			name: "a null text has no pointer",
			run: func() (string, string) {
				return strconv.FormatBool(provider.StringPointer(types.StringNull()) == nil), "true"
			},
		},
		{name: "an unknown text has no pointer", run: func() (string, string) {
			return strconv.FormatBool(provider.StringPointer(types.StringUnknown()) == nil), "true"
		}},
		{
			name: "a text has a pointer",
			run:  func() (string, string) { return *provider.StringPointer(types.StringValue("x")), "x" },
		},
		{
			name: "a nil number is null",
			run:  func() (string, string) { return provider.IntValue(nil).String(), types.Int64Null().String() },
		},
		{
			name: "a number is a value",
			run:  func() (string, string) { return strconv.FormatInt(provider.IntValue(new(3)).ValueInt64(), 10), "3" },
		},
		{
			name: "a null number has no pointer",
			run: func() (string, string) {
				return strconv.FormatBool(provider.IntPointer(types.Int64Null()) == nil), "true"
			},
		},
		{
			name: "an unknown number has no pointer",
			run: func() (string, string) {
				return strconv.FormatBool(provider.IntPointer(types.Int64Unknown()) == nil), "true"
			},
		},
		{
			name: "a number has a pointer",
			run:  func() (string, string) { return strconv.Itoa(*provider.IntPointer(types.Int64Value(4))), "4" },
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			if got, want := testCase.run(); got != want {
				t.Errorf("got %q, want %q", got, want)
			}
		})
	}
}

func TestJSONValidators(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name      string
		validator validator.String
		value     types.String
		wantError bool
	}{
		{name: "an object for an object", validator: provider.JsonObject(), value: types.StringValue(`{"a":1}`)},
		{name: "an empty object", validator: provider.JsonObject(), value: types.StringValue(`{}`), wantError: true},
		{name: "an array for an object", validator: provider.JsonObject(), value: types.StringValue(`[1]`), wantError: true},
		{name: "a scalar for an object", validator: provider.JsonObject(), value: types.StringValue(`1`), wantError: true},
		{name: "text that is not JSON", validator: provider.JsonObject(), value: types.StringValue(`{`), wantError: true},
		{name: "an array for an array", validator: provider.JsonArray(), value: types.StringValue(`[{"a":1}]`)},
		{
			name:      "an object for an array",
			validator: provider.JsonArray(),
			value:     types.StringValue(`{"a":1}`),
			wantError: true,
		},
		{name: "a null value", validator: provider.JsonObject(), value: types.StringNull()},
		{name: "an unknown value", validator: provider.JsonArray(), value: types.StringUnknown()},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			resp := validator.StringResponse{}

			testCase.validator.ValidateString(
				context.Background(),
				validator.StringRequest{Path: path.Root("settings"), ConfigValue: testCase.value},
				&resp,
			)

			if resp.Diagnostics.HasError() != testCase.wantError {
				t.Errorf("errors = %v, want an error: %t", resp.Diagnostics, testCase.wantError)
			}
		})
	}

	if got := provider.JsonObject().MarkdownDescription(context.Background()); !strings.Contains(got, "JSON object") {
		t.Errorf("description = %q, want it to name a JSON object", got)
	}
}

func TestProviderClient(t *testing.T) {
	t.Parallel()

	var diagnostics diag.Diagnostics

	if got := provider.ProviderClient(nil, &diagnostics); got != nil || diagnostics.HasError() {
		t.Errorf("providerClient(nil) = %v, %v, want nil and no error", got, diagnostics)
	}

	c := &client.Client{}

	if got := provider.ProviderClient(c, &diagnostics); got != c || diagnostics.HasError() {
		t.Errorf("providerClient = %v, %v, want the client and no error", got, diagnostics)
	}

	if got := provider.ProviderClient("not a client", &diagnostics); got != nil || !diagnostics.HasError() {
		t.Errorf("providerClient(string) = %v, %v, want nil and an error", got, diagnostics)
	}
}

func TestSchemasAreValid(t *testing.T) {
	t.Parallel()

	ctx := context.Background()
	dataProxy := provider.New("test")()

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
