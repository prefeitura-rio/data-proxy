package provider

import (
	"context"
	"data-proxy/internal/client"
	"errors"
	"fmt"
	"reflect"

	"github.com/hashicorp/terraform-plugin-framework-validators/int64validator"
	"github.com/hashicorp/terraform-plugin-framework-validators/listvalidator"
	"github.com/hashicorp/terraform-plugin-framework-validators/stringvalidator"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/booldefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

var (
	_ resource.Resource                   = (*tableResource)(nil)
	_ resource.ResourceWithConfigure      = (*tableResource)(nil)
	_ resource.ResourceWithImportState    = (*tableResource)(nil)
	_ resource.ResourceWithValidateConfig = (*tableResource)(nil)
)

// The values of the strategy of a table.
const (
	strategyFull        = "full"
	strategyPartitioned = "partitioned"
)

// tableResource is the data_proxy_table resource. A table syncs from the source to the schema, and its row policies
// limit what each reader sees.
type tableResource struct {
	client  *client.Client
	backoff client.Backoff
}

// conditionModel maps one condition block.
type conditionModel struct {
	ColumnName types.String `tfsdk:"column_name"`
	UnitType   types.String `tfsdk:"unit_type"`
}

// rlsModel maps one rls block: a rule whose conditions all must match. A rule of one condition sets column_name and
// unit_type. A rule of two or more conditions sets condition blocks.
type rlsModel struct {
	ColumnName types.String     `tfsdk:"column_name"`
	UnitType   types.String     `tfsdk:"unit_type"`
	Conditions []conditionModel `tfsdk:"condition"`
}

// encodeRule writes a rule as one rls block: flat for one condition, condition blocks for two or more.
func encodeRule(rule client.RLSRule) rlsModel {
	if len(rule.Conditions) == 1 {
		return rlsModel{
			ColumnName: types.StringValue(rule.Conditions[0].ColumnName),
			UnitType:   types.StringValue(rule.Conditions[0].UnitType),
		}
	}

	block := rlsModel{ColumnName: types.StringNull(), UnitType: types.StringNull()}
	for _, condition := range rule.Conditions {
		block.Conditions = append(block.Conditions, conditionModel{
			ColumnName: types.StringValue(condition.ColumnName),
			UnitType:   types.StringValue(condition.UnitType),
		})
	}

	return block
}

// decodeRule reads one rls block as a rule, in either form.
func decodeRule(block rlsModel) client.RLSRule {
	if len(block.Conditions) == 0 {
		return client.RLSRule{Conditions: []client.RLSCondition{{
			ColumnName: block.ColumnName.ValueString(),
			UnitType:   block.UnitType.ValueString(),
		}}}
	}

	conditions := make([]client.RLSCondition, 0, len(block.Conditions))
	for _, condition := range block.Conditions {
		conditions = append(conditions, client.RLSCondition{
			ColumnName: condition.ColumnName.ValueString(),
			UnitType:   condition.UnitType.ValueString(),
		})
	}

	return client.RLSRule{Conditions: conditions}
}

// syncAttributes declares the attributes that say how the sync copies the table.
func syncAttributes() map[string]schema.Attribute {
	return map[string]schema.Attribute{
		"strategy": schema.StringAttribute{
			Description: "`full` replaces the table on each sync, and `partitioned` updates the changed partitions.",
			Optional:    true,
			Computed:    true,
			Default:     stringdefault.StaticString(strategyFull),
			Validators:  []validator.String{stringvalidator.OneOf(strategyFull, strategyPartitioned)},
		},
		"n": schema.Int64Attribute{
			Description: "The number of newest partitions to keep. Only for the strategy `partitioned`.",
			Optional:    true,
			Validators:  []validator.Int64{int64validator.AtLeast(1)},
		},
		"fallback": schema.BoolAttribute{
			Description: "Lets the source serve the partitions that the schema doesn't store. Only for the strategy " +
				"`partitioned`.",
			Optional: true,
			Computed: true,
			Default:  booldefault.StaticBool(false),
		},
		"cache_ttl": schema.Int64Attribute{
			Description: "The seconds that the proxy caches an answer of the table. Zero turns the cache off.",
			Optional:    true,
			Validators:  []validator.Int64{int64validator.AtLeast(0)},
		},
		"partitioning": schema.StringAttribute{
			Description: "The partition transforms of DuckLake as a JSON array.",
			Optional:    true,
			Validators:  []validator.String{jsonArray()},
		},
		"sort": schema.ListAttribute{
			Description: "The columns that DuckLake sorts the table by.",
			ElementType: types.StringType,
			Optional:    true,
			Validators:  []validator.List{listvalidator.SizeAtLeast(1)},
		},
	}
}

// rlsBlock declares the rls block of a table: a policy of one condition written flat, or two or more condition blocks.
func rlsBlock() schema.ListNestedBlock {
	return schema.ListNestedBlock{
		Description: "A row policy. Write one condition as `column_name` and `unit_type`. Write two or more conditions " +
			"that all must match as `condition` blocks. A reader needs a grant for each condition, or the policy gives no " +
			"row. Policies are alternatives to each other. The schema needs a `claim`.",
		NestedObject: schema.NestedBlockObject{
			Attributes: map[string]schema.Attribute{
				attrColumnName: schema.StringAttribute{
					Description: "The column that holds the unit, for a policy of one condition.",
					Optional:    true,
					Validators:  []validator.String{stringvalidator.LengthAtLeast(1)},
				},
				attrUnitType: schema.StringAttribute{
					Description: "The type of the unit, for a policy of one condition.",
					Optional:    true,
					Validators:  []validator.String{stringvalidator.LengthAtLeast(1)},
				},
			},
			Blocks: map[string]schema.Block{
				"condition": schema.ListNestedBlock{
					Description: "A condition of the policy: the column that holds the unit, and the type of the unit.",
					NestedObject: schema.NestedBlockObject{
						Attributes: map[string]schema.Attribute{
							attrColumnName: schema.StringAttribute{
								Description: "The column that holds the unit.",
								Required:    true,
								Validators:  []validator.String{stringvalidator.LengthAtLeast(1)},
							},
							attrUnitType: schema.StringAttribute{
								Description: "The type of the unit.",
								Required:    true,
								Validators:  []validator.String{stringvalidator.LengthAtLeast(1)},
							},
						},
					},
				},
			},
		},
	}
}

// rlsProblem returns why an rls block is wrong, or the empty text. It skips a value that is not known yet.
func rlsProblem(block rlsModel) string {
	if !block.ColumnName.IsNull() || !block.UnitType.IsNull() {
		return flatBlockProblem(block)
	}

	switch len(block.Conditions) {
	case 0:
		return "An rls block needs column_name and unit_type, or two or more condition blocks."
	case 1:
		return "An rls block with one condition is written flat: use column_name and unit_type, with no condition block."
	default:
		return ""
	}
}

// flatBlockProblem returns why an rls block that has a column or a unit type is wrong, or the empty text.
func flatBlockProblem(block rlsModel) string {
	switch {
	case len(block.Conditions) > 0:
		return "An rls block has both a column and conditions. Write one condition as column_name and unit_type, or " +
			"write two or more condition blocks."
	case block.ColumnName.IsNull() || block.UnitType.IsNull():
		return "A flat rls block needs both column_name and unit_type."
	default:
		return ""
	}
}

// tableModel maps the data_proxy_table resource and data source.
type tableModel struct {
	ID           types.String `tfsdk:"id"`
	Name         types.String `tfsdk:"name"`
	Schema       types.String `tfsdk:"schema"`
	Strategy     types.String `tfsdk:"strategy"`
	N            types.Int64  `tfsdk:"n"`
	Fallback     types.Bool   `tfsdk:"fallback"`
	CacheTTL     types.Int64  `tfsdk:"cache_ttl"`
	Partitioning types.String `tfsdk:"partitioning"`
	Sort         types.List   `tfsdk:"sort"`
	RLS          []rlsModel   `tfsdk:"rls"`
	UpdatedAt    types.String `tfsdk:"updated_at"`
	UpdatedBy    types.String `tfsdk:"updated_by"`
}

// newTableResource returns the data_proxy_table resource.
func newTableResource() resource.Resource {
	return &tableResource{backoff: client.DefaultBackoff()}
}

// applyTableRow copies a row of the API and its row policies into the model. The partitioning keeps the layout of the
// current text when it holds the same JSON.
func applyTableRow(ctx context.Context, model *tableModel, row client.Table, rules []client.RLSRule) diag.Diagnostics {
	model.ID = types.StringValue(row.Name)
	model.Name = types.StringValue(row.Name)
	model.Schema = types.StringValue(row.Schema)
	model.Strategy = types.StringValue(row.Strategy)
	model.N = intValue(row.N)
	model.Fallback = types.BoolValue(row.Fallback)
	model.CacheTTL = intValue(row.CacheTTL)
	model.Partitioning = jsonValue(row.Partitioning, model.Partitioning)
	model.UpdatedAt = types.StringValue(row.UpdatedAt)
	model.UpdatedBy = types.StringValue(row.UpdatedBy)

	var diagnostics diag.Diagnostics

	if len(row.Sort) == 0 {
		model.Sort = types.ListNull(types.StringType)
	} else {
		model.Sort, diagnostics = types.ListValueFrom(ctx, types.StringType, row.Sort)
	}

	model.RLS = nil

	for _, rule := range rules {
		model.RLS = append(model.RLS, encodeRule(rule))
	}

	return diagnostics
}

// sortValues returns the sort columns of the model, or nil when none is set.
func sortValues(ctx context.Context, model tableModel) ([]string, diag.Diagnostics) {
	if model.Sort.IsNull() || model.Sort.IsUnknown() {
		return nil, nil
	}

	var columns []string

	diagnostics := model.Sort.ElementsAs(ctx, &columns, false)

	return columns, diagnostics
}

// rlsRules returns the row policies of the model, in the order of the blocks.
func rlsRules(model tableModel) []client.RLSRule {
	rules := make([]client.RLSRule, 0, len(model.RLS))
	for _, block := range model.RLS {
		rules = append(rules, decodeRule(block))
	}

	return rules
}

// Metadata names the resource with the type name of the provider and the suffix _table.
func (r *tableResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_table"
}

// Schema declares the attributes and the rls blocks of the table.
func (r *tableResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "A table that the sync copies from the source into a schema, with the row policies of its readers. " +
			"A change of the strategy, the partitioning, the sort or the row policies makes the next sync rebuild the table.",
		Attributes: withAuditAttributes(withAttributes(map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Description:   "The name of the table.",
				Computed:      true,
				PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()},
			},
			attrName: schema.StringAttribute{
				Description: "The name of the table in the source, such as `project.dataset.table`. It is unique in the " +
					"whole configuration. Changing it replaces the table and syncs it again.",
				Required:      true,
				Validators:    []validator.String{stringvalidator.LengthAtLeast(1)},
				PlanModifiers: []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			attrSchema: schema.StringAttribute{
				Description: "The physical name of the schema, such as `data_proxy_schema.example.name`. Changing it replaces " +
					"the table, except when the state holds the name of the schema without the double underscore.",
				Required:   true,
				Validators: []validator.String{stringvalidator.LengthAtLeast(1)},
				PlanModifiers: []planmodifier.String{stringplanmodifier.RequiresReplaceIf(
					func(_ context.Context, req planmodifier.StringRequest, resp *stringplanmodifier.RequiresReplaceIfFuncResponse) {
						resp.RequiresReplace = !renamedSchema(req.StateValue.ValueString(), req.PlanValue.ValueString())
					},
					"Replaces the table when the schema is another schema.",
					"Replaces the table when the schema is another schema.",
				)},
			},
		}, syncAttributes())),
		Blocks: map[string]schema.Block{
			"rls": rlsBlock(),
		},
	}
}

// ValidateConfig refuses n and fallback for a table that is not partitioned, and an rls block that mixes its two forms.
func (r *tableResource) ValidateConfig(
	ctx context.Context,
	req resource.ValidateConfigRequest,
	resp *resource.ValidateConfigResponse,
) {
	var config tableModel

	resp.Diagnostics.Append(req.Config.Get(ctx, &config)...)

	if resp.Diagnostics.HasError() {
		return
	}

	for index, block := range config.RLS {
		if problem := rlsProblem(block); problem != "" {
			resp.Diagnostics.AddAttributeError(path.Root("rls").AtListIndex(index), "Invalid rls block", problem)
		}
	}

	if config.Strategy.IsUnknown() {
		return
	}

	if config.Strategy.IsNull() || config.Strategy.ValueString() == strategyFull {
		if !config.N.IsNull() && !config.N.IsUnknown() {
			resp.Diagnostics.AddAttributeError(path.Root("n"), "Invalid n", "n needs the strategy `partitioned`.")
		}

		if config.Fallback.ValueBool() {
			resp.Diagnostics.AddAttributeError(
				path.Root("fallback"),
				"Invalid fallback",
				"fallback needs the strategy `partitioned`.",
			)
		}
	}
}

// Configure takes the client that the provider built.
func (r *tableResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.client = providerClient(req.ProviderData, &resp.Diagnostics)
}

// Create adds the table and its row policies. A failure of the policies leaves the table, which Terraform taints.
func (r *tableResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan tableModel

	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)

	columns, diagnostics := sortValues(ctx, plan)
	resp.Diagnostics.Append(diagnostics...)

	if resp.Diagnostics.HasError() {
		return
	}

	row, err := r.client.CreateTable(ctx, client.TableInput{
		Name:         plan.Name.ValueString(),
		Schema:       plan.Schema.ValueString(),
		Strategy:     plan.Strategy.ValueString(),
		N:            intPointer(plan.N),
		Fallback:     plan.Fallback.ValueBool(),
		CacheTTL:     intPointer(plan.CacheTTL),
		Partitioning: jsonRaw(plan.Partitioning),
		Sort:         columns,
	})
	if err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't create the table", "create_config", err)

		return
	}

	rules := rlsRules(plan)

	resp.Diagnostics.Append(applyTableRow(ctx, &plan, row, rules)...)
	resp.Diagnostics.Append(resp.State.Set(ctx, plan)...)

	if resp.Diagnostics.HasError() || len(rules) == 0 {
		return
	}

	err = r.client.ReplaceRLS(ctx, row.Name, rules)
	if err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't add the row policies of the table", "create_config", err)
	}
}

// Read refreshes the table and its row policies, and drops the table from the state when it no longer exists.
func (r *tableResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state tableModel

	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)

	if resp.Diagnostics.HasError() {
		return
	}

	var row client.Table

	err := client.RetryNoRow(ctx, r.backoff, func(ctx context.Context) error {
		var err error

		row, err = r.client.GetTable(ctx, state.Name.ValueString())
		if err != nil {
			return fmt.Errorf("read the table: %w", err)
		}

		return nil
	})

	switch {
	case errors.Is(err, client.ErrNoRow):
		resp.State.RemoveResource(ctx)

		return
	case err != nil:
		addAPIError(&resp.Diagnostics, "Couldn't read the table", "read_config", err)

		return
	}

	rules, err := r.client.ListRLS(ctx, row.Name)
	if err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't read the row policies of the table", "read_config", err)

		return
	}

	resp.Diagnostics.Append(applyTableRow(ctx, &state, row, rules)...)
	resp.Diagnostics.Append(resp.State.Set(ctx, state)...)
}

// Update changes the table row, and replaces the row policies when they changed.
func (r *tableResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan, state tableModel

	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)

	columns, diagnostics := sortValues(ctx, plan)
	resp.Diagnostics.Append(diagnostics...)

	if resp.Diagnostics.HasError() {
		return
	}

	row, err := r.client.UpdateTable(ctx, plan.Name.ValueString(), client.TableUpdate{
		Strategy:     plan.Strategy.ValueString(),
		N:            intPointer(plan.N),
		Fallback:     plan.Fallback.ValueBool(),
		CacheTTL:     intPointer(plan.CacheTTL),
		Partitioning: jsonRaw(plan.Partitioning),
		Sort:         columns,
	})
	if err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't update the table", "edit_config", err)

		return
	}

	rules := rlsRules(plan)
	saved := rlsRules(state)

	if !sameRules(rules, saved) {
		err := r.client.ReplaceRLS(ctx, row.Name, rules)
		if err != nil {
			addAPIError(&resp.Diagnostics, "Couldn't replace the row policies of the table", "edit_config", err)

			rules = saved
		}
	}

	resp.Diagnostics.Append(applyTableRow(ctx, &plan, row, rules)...)
	resp.Diagnostics.Append(resp.State.Set(ctx, plan)...)
}

// sameRules reports whether two lists hold the same row policies in the same order.
func sameRules(left, right []client.RLSRule) bool {
	return reflect.DeepEqual(left, right)
}

// Delete removes the table. The database removes its row policies with it.
func (r *tableResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state tableModel

	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)

	if resp.Diagnostics.HasError() {
		return
	}

	err := r.client.DeleteTable(ctx, state.Name.ValueString())
	if err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't delete the table", "delete_config", err)
	}
}

// ImportState reads the table by its name.
func (r *tableResource) ImportState(
	ctx context.Context,
	req resource.ImportStateRequest,
	resp *resource.ImportStateResponse,
) {
	resource.ImportStatePassthroughID(ctx, path.Root("name"), req, resp)
}
