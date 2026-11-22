package provider

import (
	"context"
	"data-proxy/internal/client"
	"errors"
	"fmt"

	"github.com/hashicorp/terraform-plugin-framework-validators/datasourcevalidator"
	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/datasource/schema"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

// projectDataModel is the state of the data source data_proxy_project.
type projectDataModel struct {
	ID        types.String `tfsdk:"id"`
	Name      types.String `tfsdk:"name"`
	UpdatedAt types.String `tfsdk:"updated_at"`
	UpdatedBy types.String `tfsdk:"updated_by"`
}

// schemaDataModel is the state of the data source data_proxy_schema.
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

// documentModel is the state of a data source that returns one JSON document.
type documentModel struct {
	ID       types.String `tfsdk:"id"`
	Document types.String `tfsdk:"document"`
}

// healthModel is the state of the data source data_proxy_schema_health.
type healthModel struct {
	ID           types.String `tfsdk:"id"`
	Name         types.String `tfsdk:"name"`
	SchemaExists types.Bool   `tfsdk:"schema_exists"`
	ViewCount    types.Int64  `tfsdk:"view_count"`
}

// syncStateModel is the state of the data source data_proxy_table_sync_state.
type syncStateModel struct {
	ID             types.String `tfsdk:"id"`
	Name           types.String `tfsdk:"name"`
	Schema         types.String `tfsdk:"schema"`
	Synced         types.Bool   `tfsdk:"synced"`
	Signature      types.String `tfsdk:"signature"`
	PartitionCount types.Int64  `tfsdk:"partition_count"`
}

// historyEntryModel is one change in the data source data_proxy_config_history.
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

// historyModel is the state of the data source data_proxy_config_history.
type historyModel struct {
	ID      types.String        `tfsdk:"id"`
	Schema  types.String        `tfsdk:"schema"`
	Limit   types.Int64         `tfsdk:"limit"`
	Entries []historyEntryModel `tfsdk:"entries"`
}

// defaultHistoryLimit is the number of history entries that the data source reads if no limit is set.
const defaultHistoryLimit = 100

// computedString makes a read-only text attribute.
func computedString(description string) schema.StringAttribute {
	return schema.StringAttribute{Description: description, Computed: true}
}

// requiredString makes a text attribute that the configuration must set.
func requiredString(description string) schema.StringAttribute {
	return schema.StringAttribute{Description: description, Required: true}
}

// updatedAttributes adds the columns updated_at and updated_by to a set of attributes.
func updatedAttributes(attributes map[string]schema.Attribute) map[string]schema.Attribute {
	attributes[attrUpdatedAt] = computedString("When the row last changed.")
	attributes[attrUpdatedBy] = computedString("Who last changed the row.")

	return attributes
}

// newProjectDataSource makes the data source data_proxy_project.
func newProjectDataSource() datasource.DataSource {
	return &lookup[projectDataModel]{
		suffix:      "project",
		description: "Reads a project by its name.",
		permission:  readConfig,
		attributes: updatedAttributes(map[string]schema.Attribute{
			"id":     computedString("The name of the project."),
			attrName: requiredString("The name of the project."),
		}),
		read: func(ctx context.Context, apiClient *client.Client, model *projectDataModel) error {
			row, err := apiClient.GetProject(ctx, model.Name.ValueString())
			if err != nil {
				return fmt.Errorf("read the project: %w", err)
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

// newSchemaDataSource makes the data source data_proxy_schema.
func newSchemaDataSource() datasource.DataSource {
	return &lookup[schemaDataModel]{
		suffix:      "schema",
		description: "Reads a schema by its physical name, or by its project and its short name.",
		permission:  readConfig,
		attributes: updatedAttributes(map[string]schema.Attribute{
			"id": computedString("The physical name of the schema."),
			attrName: schema.StringAttribute{
				Description: "The physical name of the schema. Give it, or the short name.",
				Optional:    true,
				Computed:    true,
			},
			attrProject: schema.StringAttribute{
				Description: "The project of the schema. With the short name, it defaults to `default`.",
				Optional:    true,
				Computed:    true,
			},
			"short_name": schema.StringAttribute{
				Description: "The short name of the schema. Give it, or the name.",
				Optional:    true,
				Computed:    true,
			},
			"source":    computedString("The source of the data."),
			"settings":  computedString("The settings of the source as a JSON object."),
			"claim":     computedString("The claim of the token that names the unit of a reader."),
			"encrypted": schema.BoolAttribute{Description: "Whether the files of the schema are encrypted.", Computed: true},
		}),
		validators: []datasource.ConfigValidator{
			datasourcevalidator.ExactlyOneOf(path.MatchRoot("name"), path.MatchRoot("short_name")),
		},
		read: readSchema,
	}
}

// readSchema reads a schema by its physical name, or by its project and its short name.
func readSchema(ctx context.Context, apiClient *client.Client, model *schemaDataModel) error {
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
		return fmt.Errorf("read the schema: %w", err)
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
}

// newTableDataSource makes the data source data_proxy_table.
func newTableDataSource() datasource.DataSource {
	return &lookup[tableModel]{
		suffix:      "table",
		description: "Reads a table and its row policies by its name.",
		permission:  readConfig,
		attributes: updatedAttributes(map[string]schema.Attribute{
			"id":       computedString("The name of the table."),
			attrName:   requiredString("The name of the table in the source."),
			attrSchema: computedString("The physical name of the schema."),
			"strategy": computedString("`full` or `partitioned`."),
			"n":        schema.Int64Attribute{Description: "The number of newest partitions to keep.", Computed: true},
			"fallback": schema.BoolAttribute{
				Description: "Whether the source serves the partitions that the schema doesn't store.",
				Computed:    true,
			},
			"cache_ttl":    schema.Int64Attribute{Description: "The seconds that the proxy caches an answer.", Computed: true},
			"partitioning": computedString("The partition transforms as a JSON array."),
			"sort": schema.ListAttribute{
				Description: "The columns that DuckLake sorts the table by.",
				ElementType: types.StringType,
				Computed:    true,
			},
			"rls": schema.ListNestedAttribute{
				Description: "The row policies in order. A policy of one condition sets `column_name` and `unit_type`. A " +
					"policy of two or more sets `condition`, and all must match.",
				Computed: true,
				NestedObject: schema.NestedAttributeObject{Attributes: map[string]schema.Attribute{
					attrColumnName: computedString("The column that holds the unit, for a policy of one condition."),
					attrUnitType:   computedString("The type of the unit, for a policy of one condition."),
					"condition": schema.ListNestedAttribute{
						Description: "The conditions of a policy of two or more.",
						Computed:    true,
						NestedObject: schema.NestedAttributeObject{Attributes: map[string]schema.Attribute{
							attrColumnName: computedString("The column that holds the unit."),
							attrUnitType:   computedString("The type of the unit."),
						}},
					},
				}},
			},
		}),
		read: func(ctx context.Context, apiClient *client.Client, model *tableModel) error {
			row, err := apiClient.GetTable(ctx, model.Name.ValueString())
			if err != nil {
				return fmt.Errorf("read the table: %w", err)
			}

			rules, err := apiClient.ListRLS(ctx, row.Name)
			if err != nil {
				return fmt.Errorf("read the row policies of the table: %w", err)
			}

			if diagnostics := applyTableRow(ctx, model, row, rules); diagnostics.HasError() {
				return fmt.Errorf("%w: %v", errMapTable, diagnostics)
			}

			return nil
		},
	}
}

// newExportConfigDataSource makes the data source data_proxy_export_config.
func newExportConfigDataSource() datasource.DataSource {
	return documentSource(
		"export_config",
		"Exports the configuration as a JSON document grouped by project, which includes the empty projects.",
		func(ctx context.Context, apiClient *client.Client) ([]byte, error) {
			return apiClient.ExportConfig(ctx)
		},
	)
}

// newSyncConfigDataSource makes the data source data_proxy_sync_config.
func newSyncConfigDataSource() datasource.DataSource {
	return documentSource(
		"sync_config",
		"Reads the flat configuration that the sync engine reads, with the physical schema names as keys.",
		func(ctx context.Context, apiClient *client.Client) ([]byte, error) { return apiClient.SyncConfig(ctx) },
	)
}

// errMapTable is a row of the API that does not fit the model of the table data source.
var errMapTable = errors.New("map the table")

// documentSource makes a data source that returns one JSON document that fetch reads.
func documentSource(
	suffix, description string,
	fetch func(context.Context, *client.Client) ([]byte, error),
) datasource.DataSource {
	return &lookup[documentModel]{
		suffix:      suffix,
		description: description,
		permission:  readConfig,
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

// newSchemaHealthDataSource makes the data source data_proxy_schema_health.
func newSchemaHealthDataSource() datasource.DataSource {
	return &lookup[healthModel]{
		suffix:      "schema_health",
		description: "Reads whether the sync has created the PostgreSQL schema.",
		permission:  readConfig,
		attributes: map[string]schema.Attribute{
			"id":            computedString("The physical name of the schema."),
			attrName:        requiredString("The physical name of the schema."),
			"schema_exists": schema.BoolAttribute{Description: "Whether the PostgreSQL schema exists.", Computed: true},
			"view_count": schema.Int64Attribute{
				Description: "The number of views in the schema. It grows after the first sync.",
				Computed:    true,
			},
		},
		read: func(ctx context.Context, apiClient *client.Client, model *healthModel) error {
			row, err := apiClient.GetHealth(ctx, model.Name.ValueString())
			if err != nil {
				return fmt.Errorf("read the health of the schema: %w", err)
			}

			*model = healthModel{
				ID:           types.StringValue(row.Name),
				Name:         types.StringValue(row.Name),
				SchemaExists: types.BoolValue(row.SchemaExists),
				ViewCount:    types.Int64Value(int64(row.ViewCount)),
			}

			return nil
		},
	}
}

// newTableSyncStateDataSource makes the data source data_proxy_table_sync_state.
func newTableSyncStateDataSource() datasource.DataSource {
	return &lookup[syncStateModel]{
		suffix:      "table_sync_state",
		description: "Reads whether the sync has published a table, and its signature and partitions.",
		permission:  readConfig,
		attributes: map[string]schema.Attribute{
			"id":              computedString("The name of the table."),
			attrName:          requiredString("The name of the table in the source."),
			attrSchema:        computedString("The physical name of the schema."),
			"synced":          schema.BoolAttribute{Description: "Whether the sync has published the table.", Computed: true},
			"signature":       computedString("The signature of the table at its last sync."),
			"partition_count": schema.Int64Attribute{Description: "The number of synced partitions.", Computed: true},
		},
		read: func(ctx context.Context, apiClient *client.Client, model *syncStateModel) error {
			row, err := apiClient.GetSyncState(ctx, model.Name.ValueString())
			if err != nil {
				return fmt.Errorf("read the sync state of the table: %w", err)
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

// newConfigHistoryDataSource makes the data source data_proxy_config_history.
func newConfigHistoryDataSource() datasource.DataSource {
	return &lookup[historyModel]{
		suffix:      "config_history",
		description: "Reads the newest changes of the configuration, newest first.",
		permission:  readConfig,
		attributes: map[string]schema.Attribute{
			"id": computedString("A fixed name."),
			attrSchema: schema.StringAttribute{
				Description: "Reads only the changes of this schema. Without it, every schema that the token can see.",
				Optional:    true,
			},
			"limit": schema.Int64Attribute{
				Description: fmt.Sprintf("The most changes to read. The default is %d.", defaultHistoryLimit),
				Optional:    true,
			},
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
				return fmt.Errorf("read the history of the configuration: %w", err)
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
