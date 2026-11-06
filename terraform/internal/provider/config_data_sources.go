package provider

import (
	"context"
	"fmt"

	"github.com/hashicorp/terraform-plugin-framework-validators/datasourcevalidator"
	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/datasource/schema"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"terraform-provider-data-proxy/internal/client"
)

// projectDataModel maps the data_proxy_project data source.
type projectDataModel struct {
	ID        types.String `tfsdk:"id"`
	Name      types.String `tfsdk:"name"`
	UpdatedAt types.String `tfsdk:"updated_at"`
	UpdatedBy types.String `tfsdk:"updated_by"`
}

// schemaDataModel maps the data_proxy_schema data source.
type schemaDataModel struct {
	ID        types.String `tfsdk:"id"`
	Project   types.String `tfsdk:"project"`
	ShortName types.String `tfsdk:"short_name"`
	Name      types.String `tfsdk:"name"`
	Source    types.String `tfsdk:"source"`
	Settings  types.String `tfsdk:"settings"`
	Claim     types.String `tfsdk:"claim"`
	Encrypted types.Bool   `tfsdk:"encrypted"`
	UpdatedAt types.String `tfsdk:"updated_at"`
	UpdatedBy types.String `tfsdk:"updated_by"`
}

// documentModel maps a data source that gives a JSON document.
type documentModel struct {
	ID       types.String `tfsdk:"id"`
	Document types.String `tfsdk:"document"`
}

// healthModel maps the data_proxy_schema_health data source.
type healthModel struct {
	ID           types.String `tfsdk:"id"`
	Name         types.String `tfsdk:"name"`
	SchemaExists types.Bool   `tfsdk:"schema_exists"`
	AccessPolicy types.Bool   `tfsdk:"access_policy"`
	ViewCount    types.Int64  `tfsdk:"view_count"`
}

// syncStateModel maps the data_proxy_table_sync_state data source.
type syncStateModel struct {
	ID             types.String `tfsdk:"id"`
	Name           types.String `tfsdk:"name"`
	Schema         types.String `tfsdk:"schema"`
	Synced         types.Bool   `tfsdk:"synced"`
	Signature      types.String `tfsdk:"signature"`
	PartitionCount types.Int64  `tfsdk:"partition_count"`
}

// historyEntryModel maps one change of the data_proxy_config_history data source.
type historyEntryModel struct {
	ChangedAt  types.String `tfsdk:"changed_at"`
	ChangedBy  types.String `tfsdk:"changed_by"`
	Action     types.String `tfsdk:"action"`
	Object     types.String `tfsdk:"object"`
	SchemaName types.String `tfsdk:"schema_name"`
	Key        types.String `tfsdk:"key"`
	OldRow     types.String `tfsdk:"old_row"`
	NewRow     types.String `tfsdk:"new_row"`
}

// historyModel maps the data_proxy_config_history data source.
type historyModel struct {
	ID      types.String        `tfsdk:"id"`
	Schema  types.String        `tfsdk:"schema"`
	Limit   types.Int64         `tfsdk:"limit"`
	Entries []historyEntryModel `tfsdk:"entries"`
}

// defaultHistoryLimit is the number of changes that the history data source reads when it gets no limit.
const defaultHistoryLimit = 100

// computedString declares a text that the provider fills.
func computedString(description string) schema.StringAttribute {
	return schema.StringAttribute{Description: description, Computed: true}
}

// requiredString declares a text that the configuration gives.
func requiredString(description string) schema.StringAttribute {
	return schema.StringAttribute{Description: description, Required: true}
}

// updatedAttributes declares the two computed attributes that every configuration row has.
func updatedAttributes(attributes map[string]schema.Attribute) map[string]schema.Attribute {
	attributes["updated_at"] = computedString("When the row last changed.")
	attributes["updated_by"] = computedString("Who last changed the row.")

	return attributes
}

// newProjectDataSource returns the data_proxy_project data source.
func newProjectDataSource() datasource.DataSource {
	return &lookup[projectDataModel]{
		suffix:      "project",
		description: "Reads a project by its name.",
		permission:  "read_config",
		attributes: updatedAttributes(map[string]schema.Attribute{
			"id":   computedString("The name of the project."),
			"name": requiredString("The name of the project."),
		}),
		read: func(ctx context.Context, apiClient *client.Client, model *projectDataModel) error {
			row, err := apiClient.GetProject(ctx, model.Name.ValueString())
			if err != nil {
				return err
			}

			*model = projectDataModel{
				ID:        types.StringValue(row.Name),
				Name:      types.StringValue(row.Name),
				UpdatedAt: types.StringValue(row.UpdatedAt),
				UpdatedBy: types.StringValue(row.UpdatedBy),
			}

			return nil
		},
	}
}

// newSchemaDataSource returns the data_proxy_schema data source.
func newSchemaDataSource() datasource.DataSource {
	return &lookup[schemaDataModel]{
		suffix:      "schema",
		description: "Reads a schema by its physical name, or by its project and its short name.",
		permission:  "read_config",
		attributes: updatedAttributes(map[string]schema.Attribute{
			"id":         computedString("The physical name of the schema."),
			"name":       schema.StringAttribute{Description: "The physical name of the schema. Give it, or the short name.", Optional: true, Computed: true},
			"project":    schema.StringAttribute{Description: "The project of the schema. With the short name, it defaults to `default`.", Optional: true, Computed: true},
			"short_name": schema.StringAttribute{Description: "The short name of the schema. Give it, or the name.", Optional: true, Computed: true},
			"source":     computedString("The source of the data."),
			"settings":   computedString("The settings of the source as a JSON object."),
			"claim":      computedString("The claim of the token that names the unit of a reader."),
			"encrypted":  schema.BoolAttribute{Description: "Whether the files of the schema are encrypted.", Computed: true},
		}),
		validators: []datasource.ConfigValidator{
			datasourcevalidator.ExactlyOneOf(path.MatchRoot("name"), path.MatchRoot("short_name")),
		},
		read: func(ctx context.Context, apiClient *client.Client, model *schemaDataModel) error {
			var row client.Schema

			var err error

			if model.Name.IsNull() {
				project := defaultProject
				if !model.Project.IsNull() {
					project = model.Project.ValueString()
				}

				row, err = apiClient.GetSchemaByParts(ctx, project, model.ShortName.ValueString())
			} else {
				row, err = apiClient.GetSchema(ctx, model.Name.ValueString())
			}

			if err != nil {
				return err
			}

			*model = schemaDataModel{
				ID:        types.StringValue(row.Name),
				Project:   types.StringValue(row.Project),
				ShortName: types.StringValue(row.ShortName),
				Name:      types.StringValue(row.Name),
				Source:    types.StringValue(row.Source),
				Settings:  jsonValue(row.Settings, types.StringNull()),
				Claim:     stringValue(row.Claim),
				Encrypted: types.BoolValue(row.Encrypted),
				UpdatedAt: types.StringValue(row.UpdatedAt),
				UpdatedBy: types.StringValue(row.UpdatedBy),
			}

			return nil
		},
	}
}

// newTableDataSource returns the data_proxy_table data source.
func newTableDataSource() datasource.DataSource {
	return &lookup[tableModel]{
		suffix:      "table",
		description: "Reads a table and its row policies by its name.",
		permission:  "read_config",
		attributes: updatedAttributes(map[string]schema.Attribute{
			"id":           computedString("The name of the table."),
			"name":         requiredString("The name of the table in the source."),
			"schema":       computedString("The physical name of the schema."),
			"strategy":     computedString("`full` or `partitioned`."),
			"n":            schema.Int64Attribute{Description: "The number of newest partitions to keep.", Computed: true},
			"fallback":     schema.BoolAttribute{Description: "Whether the source serves the partitions that the schema doesn't store.", Computed: true},
			"cache_ttl":    schema.Int64Attribute{Description: "The seconds that the proxy caches an answer.", Computed: true},
			"partitioning": computedString("The partition transforms as a JSON array."),
			"sort":         schema.ListAttribute{Description: "The columns that DuckLake sorts the table by.", ElementType: types.StringType, Computed: true},
			"rls": schema.ListNestedAttribute{
				Description: "The row policies in order.",
				Computed:    true,
				NestedObject: schema.NestedAttributeObject{Attributes: map[string]schema.Attribute{
					"column_name": computedString("The column that holds the unit."),
					"unit_type":   computedString("The type of the unit."),
				}},
			},
		}),
		read: func(ctx context.Context, apiClient *client.Client, model *tableModel) error {
			row, err := apiClient.GetTable(ctx, model.Name.ValueString())
			if err != nil {
				return err
			}

			rules, err := apiClient.ListRLS(ctx, row.Name)
			if err != nil {
				return err
			}

			if diagnostics := applyTableRow(ctx, model, row, rules); diagnostics.HasError() {
				return fmt.Errorf("map the table: %v", diagnostics)
			}

			return nil
		},
	}
}

// newExportConfigDataSource returns the data_proxy_export_config data source.
func newExportConfigDataSource() datasource.DataSource {
	return documentSource("export_config", "Exports the configuration as a JSON document grouped by project, which includes the empty projects.",
		func(ctx context.Context, apiClient *client.Client) ([]byte, error) {
			return apiClient.ExportConfig(ctx)
		})
}

// newSyncConfigDataSource returns the data_proxy_sync_config data source.
func newSyncConfigDataSource() datasource.DataSource {
	return documentSource("sync_config", "Reads the flat configuration that the sync engine reads, with the physical schema names as keys.",
		func(ctx context.Context, apiClient *client.Client) ([]byte, error) { return apiClient.SyncConfig(ctx) })
}

// documentSource builds a data source that stores a JSON document from the API in the attribute `document`.
func documentSource(suffix, description string, fetch func(context.Context, *client.Client) ([]byte, error)) datasource.DataSource {
	return &lookup[documentModel]{
		suffix:      suffix,
		description: description,
		permission:  "read_config",
		attributes: map[string]schema.Attribute{
			"id":       computedString("The name of the document."),
			"document": computedString("The JSON document. Decode it with `jsondecode`."),
		},
		read: func(ctx context.Context, apiClient *client.Client, model *documentModel) error {
			document, err := fetch(ctx, apiClient)
			if err != nil {
				return err
			}

			*model = documentModel{ID: types.StringValue(suffix), Document: types.StringValue(string(document))}

			return nil
		},
	}
}

// newSchemaHealthDataSource returns the data_proxy_schema_health data source.
func newSchemaHealthDataSource() datasource.DataSource {
	return &lookup[healthModel]{
		suffix:      "schema_health",
		description: "Reads whether the sync has created the PostgreSQL schema and its access policy table.",
		permission:  "read_config",
		attributes: map[string]schema.Attribute{
			"id":            computedString("The physical name of the schema."),
			"name":          requiredString("The physical name of the schema."),
			"schema_exists": schema.BoolAttribute{Description: "Whether the PostgreSQL schema exists.", Computed: true},
			"access_policy": schema.BoolAttribute{Description: "Whether the schema has its access policy table.", Computed: true},
			"view_count":    schema.Int64Attribute{Description: "The number of views in the schema. It grows after the first sync.", Computed: true},
		},
		read: func(ctx context.Context, apiClient *client.Client, model *healthModel) error {
			row, err := apiClient.GetHealth(ctx, model.Name.ValueString())
			if err != nil {
				return err
			}

			*model = healthModel{
				ID:           types.StringValue(row.Name),
				Name:         types.StringValue(row.Name),
				SchemaExists: types.BoolValue(row.SchemaExists),
				AccessPolicy: types.BoolValue(row.AccessPolicy),
				ViewCount:    types.Int64Value(int64(row.ViewCount)),
			}

			return nil
		},
	}
}

// newTableSyncStateDataSource returns the data_proxy_table_sync_state data source.
func newTableSyncStateDataSource() datasource.DataSource {
	return &lookup[syncStateModel]{
		suffix:      "table_sync_state",
		description: "Reads whether the sync has published a table, and its signature and partitions.",
		permission:  "read_config",
		attributes: map[string]schema.Attribute{
			"id":              computedString("The name of the table."),
			"name":            requiredString("The name of the table in the source."),
			"schema":          computedString("The physical name of the schema."),
			"synced":          schema.BoolAttribute{Description: "Whether the sync has published the table.", Computed: true},
			"signature":       computedString("The signature of the table at its last sync."),
			"partition_count": schema.Int64Attribute{Description: "The number of synced partitions.", Computed: true},
		},
		read: func(ctx context.Context, apiClient *client.Client, model *syncStateModel) error {
			row, err := apiClient.GetSyncState(ctx, model.Name.ValueString())
			if err != nil {
				return err
			}

			*model = syncStateModel{
				ID:             types.StringValue(row.Name),
				Name:           types.StringValue(row.Name),
				Schema:         types.StringValue(row.Schema),
				Synced:         types.BoolValue(row.Synced),
				Signature:      stringValue(row.Signature),
				PartitionCount: types.Int64Value(int64(row.PartitionCount)),
			}

			return nil
		},
	}
}

// newConfigHistoryDataSource returns the data_proxy_config_history data source.
func newConfigHistoryDataSource() datasource.DataSource {
	return &lookup[historyModel]{
		suffix:      "config_history",
		description: "Reads the newest changes of the configuration, newest first.",
		permission:  "read_config",
		attributes: map[string]schema.Attribute{
			"id":     computedString("A fixed name."),
			"schema": schema.StringAttribute{Description: "Reads only the changes of this schema. Without it, every schema that the token can see.", Optional: true},
			"limit":  schema.Int64Attribute{Description: fmt.Sprintf("The most changes to read. The default is %d.", defaultHistoryLimit), Optional: true},
			"entries": schema.ListNestedAttribute{
				Description: "The changes, newest first.",
				Computed:    true,
				NestedObject: schema.NestedAttributeObject{Attributes: map[string]schema.Attribute{
					"changed_at":  computedString("When the change happened."),
					"changed_by":  computedString("Who made the change."),
					"action":      computedString("`insert`, `update` or `delete`."),
					"object":      computedString("`schemas`, `tables` or `rls`."),
					"schema_name": computedString("The schema of the change."),
					"key":         computedString("The key of the changed row."),
					"old_row":     computedString("The row before the change, as JSON."),
					"new_row":     computedString("The row after the change, as JSON."),
				}},
			},
		},
		read: func(ctx context.Context, apiClient *client.Client, model *historyModel) error {
			limit := defaultHistoryLimit
			if !model.Limit.IsNull() {
				limit = int(model.Limit.ValueInt64())
			}

			rows, err := apiClient.ListHistory(ctx, model.Schema.ValueString(), limit)
			if err != nil {
				return err
			}

			model.ID = types.StringValue("config_history")
			model.Entries = make([]historyEntryModel, 0, len(rows))

			for _, row := range rows {
				model.Entries = append(model.Entries, historyEntryModel{
					ChangedAt:  types.StringValue(row.ChangedAt),
					ChangedBy:  stringValue(row.ChangedBy),
					Action:     types.StringValue(row.Action),
					Object:     types.StringValue(row.Object),
					SchemaName: types.StringValue(row.SchemaName),
					Key:        types.StringValue(row.Key),
					OldRow:     jsonValue(row.OldRow, types.StringNull()),
					NewRow:     jsonValue(row.NewRow, types.StringNull()),
				})
			}

			return nil
		},
	}
}
