package provider

import (
	"context"
	"errors"

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

	"terraform-provider-data-proxy/internal/client"
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

// rlsModel maps one rls block.
type rlsModel struct {
	ColumnName types.String `tfsdk:"column_name"`
	UnitType   types.String `tfsdk:"unit_type"`
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
	return &tableResource{backoff: client.DefaultBackoff}
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
		model.RLS = append(model.RLS, rlsModel{
			ColumnName: types.StringValue(rule.ColumnName),
			UnitType:   types.StringValue(rule.UnitType),
		})
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

// rlsRules returns the row policies of the model in order.
func rlsRules(model tableModel) []client.RLSRule {
	rules := make([]client.RLSRule, 0, len(model.RLS))
	for _, block := range model.RLS {
		rules = append(rules, client.RLSRule{
			ColumnName: block.ColumnName.ValueString(),
			UnitType:   block.UnitType.ValueString(),
		})
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
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Description:   "The name of the table.",
				Computed:      true,
				PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()},
			},
			"name": schema.StringAttribute{
				Description:   "The name of the table in the source, such as `project.dataset.table`. It is unique in the whole configuration. Changing it replaces the table and syncs it again.",
				Required:      true,
				Validators:    []validator.String{stringvalidator.LengthAtLeast(1)},
				PlanModifiers: []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"schema": schema.StringAttribute{
				Description:   "The physical name of the schema, such as `data_proxy_schema.example.name`. Changing it replaces the table.",
				Required:      true,
				Validators:    []validator.String{stringvalidator.LengthAtLeast(1)},
				PlanModifiers: []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
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
				Description: "Lets the source serve the partitions that the schema doesn't store. Only for the strategy `partitioned`.",
				Optional:    true,
				Computed:    true,
				Default:     booldefault.StaticBool(false),
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
			"updated_at": schema.StringAttribute{
				Description: "When the row last changed.",
				Computed:    true,
			},
			"updated_by": schema.StringAttribute{
				Description: "Who last changed the row.",
				Computed:    true,
			},
		},
		Blocks: map[string]schema.Block{
			"rls": schema.ListNestedBlock{
				Description: "A row policy. A reader sees the rows whose column holds a unit of the reader. The order is the order of the unit identifier. The schema needs a `claim`.",
				NestedObject: schema.NestedBlockObject{
					Attributes: map[string]schema.Attribute{
						"column_name": schema.StringAttribute{
							Description: "The column that holds the unit.",
							Required:    true,
							Validators:  []validator.String{stringvalidator.LengthAtLeast(1)},
						},
						"unit_type": schema.StringAttribute{
							Description: "The type of the unit, unique in the table with the column.",
							Required:    true,
							Validators:  []validator.String{stringvalidator.LengthAtLeast(1)},
						},
					},
				},
			},
		},
	}
}

// ValidateConfig refuses n and fallback for a table that is not partitioned.
func (r *tableResource) ValidateConfig(ctx context.Context, req resource.ValidateConfigRequest, resp *resource.ValidateConfigResponse) {
	var config tableModel

	resp.Diagnostics.Append(req.Config.Get(ctx, &config)...)

	if resp.Diagnostics.HasError() || config.Strategy.IsUnknown() {
		return
	}

	if config.Strategy.IsNull() || config.Strategy.ValueString() == strategyFull {
		if !config.N.IsNull() && !config.N.IsUnknown() {
			resp.Diagnostics.AddAttributeError(path.Root("n"), "Invalid n", "n needs the strategy `partitioned`.")
		}

		if config.Fallback.ValueBool() {
			resp.Diagnostics.AddAttributeError(path.Root("fallback"), "Invalid fallback", "fallback needs the strategy `partitioned`.")
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

	if err := r.client.ReplaceRLS(ctx, row.Name, rules); err != nil {
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

	err := client.RetryNoRow(ctx, r.backoff, func(ctx context.Context) (err error) {
		row, err = r.client.GetTable(ctx, state.Name.ValueString())

		return err
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
		if err := r.client.ReplaceRLS(ctx, row.Name, rules); err != nil {
			addAPIError(&resp.Diagnostics, "Couldn't replace the row policies of the table", "edit_config", err)

			rules = saved
		}
	}

	resp.Diagnostics.Append(applyTableRow(ctx, &plan, row, rules)...)
	resp.Diagnostics.Append(resp.State.Set(ctx, plan)...)
}

// sameRules reports whether two lists hold the same row policies in the same order.
func sameRules(left, right []client.RLSRule) bool {
	if len(left) != len(right) {
		return false
	}

	for i := range left {
		if left[i] != right[i] {
			return false
		}
	}

	return true
}

// Delete removes the table. The database removes its row policies with it.
func (r *tableResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state tableModel

	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)

	if resp.Diagnostics.HasError() {
		return
	}

	if err := r.client.DeleteTable(ctx, state.Name.ValueString()); err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't delete the table", "delete_config", err)
	}
}

// ImportState reads the table by its name.
func (r *tableResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	resource.ImportStatePassthroughID(ctx, path.Root("name"), req, resp)
}
