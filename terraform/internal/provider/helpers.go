package provider

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"reflect"

	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"terraform-provider-data-proxy/internal/client"
)

// providerClient returns the client that the provider gave to a resource or a data source. It returns nil before the
// provider is configured, and it adds an error when the provider data has another type.
func providerClient(data any, diagnostics *diag.Diagnostics) *client.Client {
	if data == nil {
		return nil
	}

	apiClient, ok := data.(*client.Client)
	if !ok {
		diagnostics.AddError("Unexpected provider data",
			fmt.Sprintf("The object wants a *client.Client, and it got %T. Report this bug to the developers of the provider.", data))

		return nil
	}

	return apiClient
}

// stringValue turns an optional text of the API into an attribute value, and a missing text into null.
func stringValue(text *string) types.String {
	if text == nil {
		return types.StringNull()
	}

	return types.StringValue(*text)
}

// stringPointer turns an attribute value into an optional text, and null or unknown into nil.
func stringPointer(value types.String) *string {
	if value.IsNull() || value.IsUnknown() {
		return nil
	}

	text := value.ValueString()

	return &text
}

// intValue turns an optional number of the API into an attribute value, and a missing number into null.
func intValue(number *int) types.Int64 {
	if number == nil {
		return types.Int64Null()
	}

	return types.Int64Value(int64(*number))
}

// intPointer turns an attribute value into an optional number, and null or unknown into nil.
func intPointer(value types.Int64) *int {
	if value.IsNull() || value.IsUnknown() {
		return nil
	}

	number := int(value.ValueInt64())

	return &number
}

// compactJSON returns the JSON text without insignificant space, or nil for an empty value or a JSON null.
func compactJSON(raw json.RawMessage) json.RawMessage {
	var buffer bytes.Buffer

	if len(raw) == 0 || json.Compact(&buffer, raw) != nil || buffer.String() == "null" {
		return nil
	}

	return buffer.Bytes()
}

// jsonValue turns a JSON value of the API into an attribute text, and a missing or null value into null. When the
// state already holds the same JSON in another layout, it keeps the state text, so that formatting never shows as a
// change.
func jsonValue(raw json.RawMessage, current types.String) types.String {
	compact := compactJSON(raw)
	if compact == nil {
		return types.StringNull()
	}

	if !current.IsNull() && !current.IsUnknown() && sameJSON(current.ValueString(), string(compact)) {
		return current
	}

	return types.StringValue(string(compact))
}

// jsonRaw turns an attribute text into a JSON value for the API, and null or unknown into nil.
func jsonRaw(value types.String) json.RawMessage {
	if value.IsNull() || value.IsUnknown() {
		return nil
	}

	return json.RawMessage(value.ValueString())
}

// sameJSON reports whether two texts hold the same JSON value, whatever their layout.
func sameJSON(left, right string) bool {
	var leftValue, rightValue any

	if json.Unmarshal([]byte(left), &leftValue) != nil || json.Unmarshal([]byte(right), &rightValue) != nil {
		return false
	}

	return reflect.DeepEqual(leftValue, rightValue)
}

// jsonKind is a validator that accepts a JSON object, or a JSON array if array is true.
type jsonKind struct {
	array bool
}

// jsonObject makes a validator that accepts a text with a JSON object.
func jsonObject() validator.String {
	return jsonKind{}
}

// jsonArray makes a validator that accepts a text with a JSON array.
func jsonArray() validator.String {
	return jsonKind{array: true}
}

// Description says what the validator accepts.
func (jk jsonKind) Description(context.Context) string {
	if jk.array {
		return "the value must be a JSON array"
	}

	return "the value must be a JSON object with at least one key"
}

// MarkdownDescription says what the validator accepts.
func (jk jsonKind) MarkdownDescription(ctx context.Context) string {
	return jk.Description(ctx)
}

// ValidateString checks the text of a known value.
func (jk jsonKind) ValidateString(ctx context.Context, req validator.StringRequest, resp *validator.StringResponse) {
	if req.ConfigValue.IsNull() || req.ConfigValue.IsUnknown() {
		return
	}

	var value any

	if err := json.Unmarshal([]byte(req.ConfigValue.ValueString()), &value); err != nil {
		resp.Diagnostics.AddAttributeError(req.Path, "Invalid JSON", fmt.Sprintf("%s: %s.", jk.Description(ctx), err))

		return
	}

	switch typed := value.(type) {
	case []any:
		if !jk.array {
			resp.Diagnostics.AddAttributeError(req.Path, "Invalid JSON", jk.Description(ctx)+".")
		}
	case map[string]any:
		if jk.array || len(typed) == 0 {
			resp.Diagnostics.AddAttributeError(req.Path, "Invalid JSON", jk.Description(ctx)+".")
		}
	default:
		resp.Diagnostics.AddAttributeError(req.Path, "Invalid JSON", jk.Description(ctx)+".")
	}
}
