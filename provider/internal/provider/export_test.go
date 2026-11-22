package provider

import (
	"context"
	"data-proxy/internal/client"
	"encoding/json"
	"regexp"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/datasource/schema"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

// This file is part of the package, not of the test package, so it can see the names that the tests of provider_test
// need and that the package does not export. It exports nothing to a user of the package.

// AddAPIError is addAPIError, for the tests.
func AddAPIError(diagnostics *diag.Diagnostics, summary, permission string, err error) {
	addAPIError(diagnostics, summary, permission, err)
}

// ApplySchemaRow is applySchemaRow, for the tests.
func ApplySchemaRow(model *schemaModel, row client.Schema) {
	applySchemaRow(model, row)
}

// ApplyTableRow is applyTableRow, for the tests.
func ApplyTableRow(ctx context.Context, model *tableModel, row client.Table, rules []client.RLSRule) diag.Diagnostics {
	return applyTableRow(ctx, model, row, rules)
}

// BlockOf is blockOf, for the tests.
func BlockOf(rule client.RLSRule) rlsModel {
	return blockOf(rule)
}

// CheckReadConfig is checkReadConfig, for the tests.
func CheckReadConfig(clientID string, permissions []string) diag.Diagnostics {
	return checkReadConfig(clientID, permissions)
}

// CompactJSON is compactJSON, for the tests.
func CompactJSON(raw json.RawMessage) json.RawMessage {
	return compactJSON(raw)
}

// ConditionModel is conditionModel, for the tests.
type ConditionModel = conditionModel

// DocumentModel is documentModel, for the tests.
type DocumentModel = documentModel

// GrantModel is grantModel, for the tests.
type GrantModel = grantModel

// GrantResource is grantResource, for the tests.
type GrantResource = grantResource

// GrantScope returns the pattern of the scope of a grant.
func GrantScope() *regexp.Regexp { return grantScope }

// HealthModel is healthModel, for the tests.
type HealthModel = healthModel

// HistoryModel is historyModel, for the tests.
type HistoryModel = historyModel

// IntPointer is intPointer, for the tests.
func IntPointer(value types.Int64) *int {
	return intPointer(value)
}

// IntValue is intValue, for the tests.
func IntValue(number *int) types.Int64 {
	return intValue(number)
}

// JsonArray is jsonArray, for the tests.
func JsonArray() validator.String {
	return jsonArray()
}

// JsonObject is jsonObject, for the tests.
func JsonObject() validator.String {
	return jsonObject()
}

// JsonRaw is jsonRaw, for the tests.
func JsonRaw(value types.String) json.RawMessage {
	return jsonRaw(value)
}

// JsonValue is jsonValue, for the tests.
func JsonValue(raw json.RawMessage, current types.String) types.String {
	return jsonValue(raw, current)
}

// LegacySchemaName is legacySchemaName, for the tests.
func LegacySchemaName(name string) string {
	return legacySchemaName(name)
}

// NewConfigHistoryDataSource is newConfigHistoryDataSource, for the tests.
func NewConfigHistoryDataSource() datasource.DataSource {
	return newConfigHistoryDataSource()
}

// NewExportConfigDataSource is newExportConfigDataSource, for the tests.
func NewExportConfigDataSource() datasource.DataSource {
	return newExportConfigDataSource()
}

// NewPermissionsDataSource is newPermissionsDataSource, for the tests.
func NewPermissionsDataSource() datasource.DataSource {
	return newPermissionsDataSource()
}

// NewProjectDataSource is newProjectDataSource, for the tests.
func NewProjectDataSource() datasource.DataSource {
	return newProjectDataSource()
}

// NewSchemaDataSource is newSchemaDataSource, for the tests.
func NewSchemaDataSource() datasource.DataSource {
	return newSchemaDataSource()
}

// NewSchemaHealthDataSource is newSchemaHealthDataSource, for the tests.
func NewSchemaHealthDataSource() datasource.DataSource {
	return newSchemaHealthDataSource()
}

// NewSchemaResource is newSchemaResource, for the tests.
func NewSchemaResource() resource.Resource {
	return newSchemaResource()
}

// NewSyncConfigDataSource is newSyncConfigDataSource, for the tests.
func NewSyncConfigDataSource() datasource.DataSource {
	return newSyncConfigDataSource()
}

// NewTableDataSource is newTableDataSource, for the tests.
func NewTableDataSource() datasource.DataSource {
	return newTableDataSource()
}

// NewTableSyncStateDataSource is newTableSyncStateDataSource, for the tests.
func NewTableSyncStateDataSource() datasource.DataSource {
	return newTableSyncStateDataSource()
}

// ParseGrantID is parseGrantID, for the tests.
func ParseGrantID(importID string) (client.Grant, error) {
	return parseGrantID(importID)
}

// PermissionsDataSource is permissionsDataSource, for the tests.
type PermissionsDataSource = permissionsDataSource

// PermissionsModel is permissionsModel, for the tests.
type PermissionsModel = permissionsModel

// PhysicalSchemaName is physicalSchemaName, for the tests.
func PhysicalSchemaName(project, shortName string) string {
	return physicalSchemaName(project, shortName)
}

// ProjectDataModel is projectDataModel, for the tests.
type ProjectDataModel = projectDataModel

// ProjectModel is projectModel, for the tests.
type ProjectModel = projectModel

// ProjectResource is projectResource, for the tests.
type ProjectResource = projectResource

// ProviderClient is providerClient, for the tests.
func ProviderClient(data any, diagnostics *diag.Diagnostics) *client.Client {
	return providerClient(data, diagnostics)
}

// ProviderModel is providerModel, for the tests.
type ProviderModel = providerModel

// ReadSettings is readSettings, for the tests.
func ReadSettings(ctx context.Context, config providerModel, getenv func(string) string) (settings, diag.Diagnostics) {
	return readSettings(ctx, config, getenv)
}

// RenamedSchema is renamedSchema, for the tests.
func RenamedSchema(state, plan string) bool { return renamedSchema(state, plan) }

// ReservedProjects is reservedProjects, for the tests.
func ReservedProjects() []string {
	return reservedProjects()
}

// RlsModel is rlsModel, for the tests.
type RlsModel = rlsModel

// RlsProblem is rlsProblem, for the tests.
func RlsProblem(block rlsModel) string {
	return rlsProblem(block)
}

// RlsRules is rlsRules, for the tests.
func RlsRules(model tableModel) []client.RLSRule {
	return rlsRules(model)
}

// RuleOf is ruleOf, for the tests.
func RuleOf(block rlsModel) client.RLSRule {
	return ruleOf(block)
}

// SameJSON is sameJSON, for the tests.
func SameJSON(left, right string) bool { return sameJSON(left, right) }

// SameRules is sameRules, for the tests.
func SameRules(left, right []client.RLSRule) bool { return sameRules(left, right) }

// SchemaDataModel is schemaDataModel, for the tests.
type SchemaDataModel = schemaDataModel

// SchemaModel is schemaModel, for the tests.
type SchemaModel = schemaModel

// SchemaProblem is schemaProblem, for the tests.
func SchemaProblem(project, shortName string) string { return schemaProblem(project, shortName) }

// SchemaResource is schemaResource, for the tests.
type SchemaResource = schemaResource

// Settings is settings, for the tests.
type Settings = settings

// SplitScopes is splitScopes, for the tests.
func SplitScopes(value string) []string {
	return splitScopes(value)
}

// StringPointer is stringPointer, for the tests.
func StringPointer(value types.String) *string {
	return stringPointer(value)
}

// StringValue is stringValue, for the tests.
func StringValue(text *string) types.String {
	return stringValue(text)
}

// SyncStateModel is syncStateModel, for the tests.
type SyncStateModel = syncStateModel

// TableModel is tableModel, for the tests.
type TableModel = tableModel

// TableResource is tableResource, for the tests.
type TableResource = tableResource

// NewLookup makes a lookup data source with the suffix, the attributes and the validators that a test gives.
func NewLookup(
	suffix string,
	attributes map[string]schema.Attribute,
	validators []datasource.ConfigValidator,
) datasource.DataSourceWithConfigValidators {
	return &lookup[struct{}]{suffix: suffix, attributes: attributes, validators: validators}
}

// GrantResourceWithNoRetry returns the grant resource without a retry delay, so that a test does not wait.
func GrantResourceWithNoRetry() *grantResource { return &grantResource{backoff: client.Backoff{}} }

// ProjectResourceWithNoRetry returns the project resource without a retry delay.
func ProjectResourceWithNoRetry() *projectResource {
	return &projectResource{backoff: client.Backoff{}}
}

// SchemaResourceWithNoRetry returns the schema resource without a retry delay.
func SchemaResourceWithNoRetry() *schemaResource { return &schemaResource{backoff: client.Backoff{}} }

// TableResourceWithNoRetry returns the table resource without a retry delay.
func TableResourceWithNoRetry() *tableResource { return &tableResource{backoff: client.Backoff{}} }

// NewSettings builds the settings that ReadSettings returns.
func NewSettings(endpoint string, credentials client.Credentials) Settings {
	return settings{endpoint: endpoint, credentials: credentials}
}

// Endpoint returns the endpoint of the settings.
func (s settings) Endpoint() string { return s.endpoint }

// Credentials returns the credentials of the settings.
func (s settings) Credentials() client.Credentials { return s.credentials }

// HasClient reports whether the permissions data source got a client.
func (p *permissionsDataSource) HasClient() bool { return p.client != nil }

// ProjectName returns the pattern of the name of a project.
func ProjectName() *regexp.Regexp { return projectName }
