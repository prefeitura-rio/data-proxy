package provider

import (
	"context"
	"errors"
	"regexp"
	"time"

	"github.com/hashicorp/terraform-plugin-framework-timeouts/resource/timeouts"
	"github.com/hashicorp/terraform-plugin-framework-validators/stringvalidator"
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
	_ resource.Resource                = (*schemaResource)(nil)
	_ resource.ResourceWithConfigure   = (*schemaResource)(nil)
	_ resource.ResourceWithImportState = (*schemaResource)(nil)

	// shortName is the pattern of the short name of a schema.
	shortName = regexp.MustCompile(`^[a-z][a-z0-9_]{0,55}$`)
)

// The time that the create and the delete of a schema wait for the sync to provision or to drop it.
const (
	schemaCreateTimeout = 5 * time.Minute
	schemaDeleteTimeout = 10 * time.Minute
)

// schemaResource is the data_proxy_schema resource. A schema holds the tables of one source and the access policy
// of its readers.
type schemaResource struct {
	client  *client.Client
	backoff client.Backoff
}

// schemaModel maps the data_proxy_schema resource and data source.
type schemaModel struct {
	ID                 types.String   `tfsdk:"id"`
	Project            types.String   `tfsdk:"project"`
	ShortName          types.String   `tfsdk:"short_name"`
	Name               types.String   `tfsdk:"name"`
	Source             types.String   `tfsdk:"source"`
	Settings           types.String   `tfsdk:"settings"`
	Claim              types.String   `tfsdk:"claim"`
	Encrypted          types.Bool     `tfsdk:"encrypted"`
	DeletionProtection types.Bool     `tfsdk:"deletion_protection"`
	UpdatedAt          types.String   `tfsdk:"updated_at"`
	UpdatedBy          types.String   `tfsdk:"updated_by"`
	Timeouts           timeouts.Value `tfsdk:"timeouts"`
}

// newSchemaResource returns the data_proxy_schema resource.
func newSchemaResource() resource.Resource {
	return &schemaResource{backoff: client.DefaultBackoff}
}

// applySchemaRow copies a row of the API into the model. The settings keep the layout of the current text when it
// holds the same JSON.
func applySchemaRow(model *schemaModel, row client.Schema) {
	model.ID = types.StringValue(row.Name)
	model.Project = types.StringValue(row.Project)
	model.ShortName = types.StringValue(row.ShortName)
	model.Name = types.StringValue(row.Name)
	model.Source = types.StringValue(row.Source)
	model.Settings = jsonValue(row.Settings, model.Settings)
	model.Claim = stringValue(row.Claim)
	model.Encrypted = types.BoolValue(row.Encrypted)
	model.UpdatedAt = types.StringValue(row.UpdatedAt)
	model.UpdatedBy = types.StringValue(row.UpdatedBy)
}

// Metadata names the resource with the type name of the provider and the suffix _schema.
func (r *schemaResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_schema"
}

// Schema declares the attributes of the schema.
func (r *schemaResource) Schema(ctx context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "A schema of a project. The sync creates its PostgreSQL schema, which takes seconds, and a delete drops its data.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Description:   "The physical name of the schema.",
				Computed:      true,
				PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()},
			},
			"project": schema.StringAttribute{
				Description:   "The project of the schema. Changing it replaces the schema and drops its data.",
				Optional:      true,
				Computed:      true,
				Default:       stringdefault.StaticString(defaultProject),
				Validators:    []validator.String{stringvalidator.RegexMatches(projectName, "must be a project name")},
				PlanModifiers: []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"short_name": schema.StringAttribute{
				Description:   "The name of the schema inside its project. Changing it replaces the schema and drops its data.",
				Required:      true,
				Validators:    []validator.String{stringvalidator.RegexMatches(shortName, "must be a lowercase letter and up to 55 lowercase letters, digits and underscores")},
				PlanModifiers: []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"name": schema.StringAttribute{
				Description:   "The physical name: the short name in the project `default`, and the project, an underscore and the short name in another one. Tables and access policies use it.",
				Computed:      true,
				PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()},
			},
			"source": schema.StringAttribute{
				Description: "The source of the data, such as `bigquery`.",
				Optional:    true,
				Computed:    true,
				Default:     stringdefault.StaticString("bigquery"),
				Validators:  []validator.String{stringvalidator.LengthAtLeast(1)},
			},
			"settings": schema.StringAttribute{
				Description: "The settings of the source as a JSON object, such as `jsonencode({...})`. Don't put a secret in it.",
				Optional:    true,
				Validators:  []validator.String{jsonObject()},
			},
			"claim": schema.StringAttribute{
				Description: "The claim of the token that names the unit of a reader, such as `preferred_username`. A table with a row policy needs it.",
				Optional:    true,
				Validators:  []validator.String{stringvalidator.LengthAtLeast(1)},
			},
			"encrypted": schema.BoolAttribute{
				Description: "Whether the files of the schema are encrypted.",
				Optional:    true,
				Computed:    true,
				Default:     booldefault.StaticBool(false),
			},
			"deletion_protection": schema.BoolAttribute{
				Description: "Refuses the delete of the schema while it is true, because a delete drops the data. Only Terraform reads it.",
				Optional:    true,
				Computed:    true,
				Default:     booldefault.StaticBool(true),
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
			"timeouts": timeouts.Block(ctx, timeouts.Opts{Create: true, Delete: true}),
		},
	}
}

// Configure takes the client that the provider built.
func (r *schemaResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.client = providerClient(req.ProviderData, &resp.Diagnostics)
}

// Create adds the schema, saves it, and waits for the sync to provision it. A create timeout of 0s skips the wait.
func (r *schemaResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan schemaModel

	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)

	if resp.Diagnostics.HasError() {
		return
	}

	row, err := r.client.CreateSchema(ctx, client.SchemaInput{
		Project:   plan.Project.ValueString(),
		ShortName: plan.ShortName.ValueString(),
		Source:    plan.Source.ValueString(),
		Settings:  jsonRaw(plan.Settings),
		Claim:     stringPointer(plan.Claim),
		Encrypted: plan.Encrypted.ValueBool(),
	})
	if err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't create the schema", "create_config", err)

		return
	}

	applySchemaRow(&plan, row)
	resp.Diagnostics.Append(resp.State.Set(ctx, plan)...)

	if resp.Diagnostics.HasError() {
		return
	}

	limit, diagnostics := plan.Timeouts.Create(ctx, schemaCreateTimeout)
	resp.Diagnostics.Append(diagnostics...)

	if resp.Diagnostics.HasError() || limit <= 0 {
		return
	}

	waitCtx, cancel := context.WithTimeout(ctx, limit)
	defer cancel()

	if err := r.client.WaitProvisioned(waitCtx, row.Name); err != nil {
		addAPIError(&resp.Diagnostics, "The schema is not ready", "", err)
	}
}

// Read refreshes the schema, and drops it from the state when it no longer exists.
func (r *schemaResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state schemaModel

	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)

	if resp.Diagnostics.HasError() {
		return
	}

	var row client.Schema

	err := client.RetryNoRow(ctx, r.backoff, func(ctx context.Context) (err error) {
		row, err = r.client.GetSchema(ctx, state.Name.ValueString())

		return err
	})

	switch {
	case errors.Is(err, client.ErrNoRow):
		resp.State.RemoveResource(ctx)
	case err != nil:
		addAPIError(&resp.Diagnostics, "Couldn't read the schema", "read_config", err)
	default:
		applySchemaRow(&state, row)
		resp.Diagnostics.Append(resp.State.Set(ctx, state)...)
	}
}

// Update changes the source, the settings, the claim and the encryption flag. The name never changes.
func (r *schemaResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan schemaModel

	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)

	if resp.Diagnostics.HasError() {
		return
	}

	row, err := r.client.UpdateSchema(ctx, plan.Name.ValueString(), client.SchemaUpdate{
		Source:    plan.Source.ValueString(),
		Settings:  jsonRaw(plan.Settings),
		Claim:     stringPointer(plan.Claim),
		Encrypted: plan.Encrypted.ValueBool(),
	})
	if err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't update the schema", "edit_config", err)

		return
	}

	applySchemaRow(&plan, row)
	resp.Diagnostics.Append(resp.State.Set(ctx, plan)...)
}

// Delete removes the schema, and waits for the sync to drop it. A delete timeout of 0s skips the wait. It refuses
// while deletion_protection is true.
func (r *schemaResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state schemaModel

	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)

	if resp.Diagnostics.HasError() {
		return
	}

	if state.DeletionProtection.ValueBool() {
		resp.Diagnostics.AddError("The schema has deletion protection",
			"A delete drops the data of the schema. Set deletion_protection = false, apply, and then delete the schema.")

		return
	}

	if err := r.client.DeleteSchema(ctx, state.Name.ValueString()); err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't delete the schema", "delete_config", err)

		return
	}

	limit, diagnostics := state.Timeouts.Delete(ctx, schemaDeleteTimeout)
	resp.Diagnostics.Append(diagnostics...)

	if resp.Diagnostics.HasError() || limit <= 0 {
		return
	}

	waitCtx, cancel := context.WithTimeout(ctx, limit)
	defer cancel()

	if err := r.client.WaitDeprovisioned(waitCtx, state.Name.ValueString()); err != nil {
		addAPIError(&resp.Diagnostics, "The schema is not dropped yet", "", err)
	}
}

// ImportState reads the schema by its physical name. The imported schema has deletion protection.
func (r *schemaResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	resource.ImportStatePassthroughID(ctx, path.Root("name"), req, resp)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("deletion_protection"), true)...)
}
