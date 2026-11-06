package provider

import (
	"context"
	"errors"
	"regexp"

	"github.com/hashicorp/terraform-plugin-framework-validators/stringvalidator"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"terraform-provider-data-proxy/internal/client"
)

var (
	_ resource.Resource                = (*projectResource)(nil)
	_ resource.ResourceWithConfigure   = (*projectResource)(nil)
	_ resource.ResourceWithImportState = (*projectResource)(nil)

	// projectName is the pattern of the name of a project.
	projectName = regexp.MustCompile(`^[a-z][a-z0-9]{0,29}$`)
)

// defaultProject is the project that every database has. A resource can't create it.
const defaultProject = "default"

// projectResource is the data_proxy_project resource. A project groups schemas under one name.
type projectResource struct {
	client  *client.Client
	backoff client.Backoff
}

// projectModel maps the data_proxy_project resource.
type projectModel struct {
	ID        types.String `tfsdk:"id"`
	Name      types.String `tfsdk:"name"`
	UpdatedAt types.String `tfsdk:"updated_at"`
	UpdatedBy types.String `tfsdk:"updated_by"`
}

// newProjectResource returns the data_proxy_project resource.
func newProjectResource() resource.Resource {
	return &projectResource{backoff: client.DefaultBackoff}
}

// projectState maps a row of the API to the model.
func projectState(row client.Project) projectModel {
	return projectModel{
		ID:        types.StringValue(row.Name),
		Name:      types.StringValue(row.Name),
		UpdatedAt: types.StringValue(row.UpdatedAt),
		UpdatedBy: types.StringValue(row.UpdatedBy),
	}
}

// Metadata names the resource with the type name of the provider and the suffix _project.
func (r *projectResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_project"
}

// Schema declares the attributes of the project.
func (r *projectResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "A project that groups schemas. The project `default` exists already, so import it with a data source instead.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Description:   "The name of the project.",
				Computed:      true,
				PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()},
			},
			"name": schema.StringAttribute{
				Description: "The name of the project: a lowercase letter, then up to 29 lowercase letters and digits. Changing it replaces the project.",
				Required:    true,
				Validators: []validator.String{
					stringvalidator.RegexMatches(projectName, "must be a lowercase letter and up to 29 lowercase letters and digits"),
					stringvalidator.NoneOf(defaultProject),
				},
				PlanModifiers: []planmodifier.String{stringplanmodifier.RequiresReplace()},
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
	}
}

// Configure takes the client that the provider built.
func (r *projectResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.client = providerClient(req.ProviderData, &resp.Diagnostics)
}

// Create adds the project.
func (r *projectResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan projectModel

	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)

	if resp.Diagnostics.HasError() {
		return
	}

	row, err := r.client.CreateProject(ctx, plan.Name.ValueString())
	if err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't create the project", "create_config", err)

		return
	}

	resp.Diagnostics.Append(resp.State.Set(ctx, projectState(row))...)
}

// Read refreshes the project, and drops it from the state when it no longer exists.
func (r *projectResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state projectModel

	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)

	if resp.Diagnostics.HasError() {
		return
	}

	var row client.Project

	err := client.RetryNoRow(ctx, r.backoff, func(ctx context.Context) (err error) {
		row, err = r.client.GetProject(ctx, state.Name.ValueString())

		return err
	})

	switch {
	case errors.Is(err, client.ErrNoRow):
		resp.State.RemoveResource(ctx)
	case err != nil:
		addAPIError(&resp.Diagnostics, "Couldn't read the project", "read_config", err)
	default:
		resp.Diagnostics.Append(resp.State.Set(ctx, projectState(row))...)
	}
}

// Update never runs, because the name is the only attribute and a change of it replaces the project.
func (r *projectResource) Update(_ context.Context, _ resource.UpdateRequest, resp *resource.UpdateResponse) {
	resp.Diagnostics.AddError("A project can't change", "The name of a project is its only attribute. Report this bug to the developers of the provider.")
}

// Delete removes the project. The database refuses it while the project has schemas.
func (r *projectResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state projectModel

	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)

	if resp.Diagnostics.HasError() {
		return
	}

	if err := r.client.DeleteProject(ctx, state.Name.ValueString()); err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't delete the project", "delete_config", err)
	}
}

// ImportState reads the project by its name.
func (r *projectResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	resource.ImportStatePassthroughID(ctx, path.Root("name"), req, resp)
}
