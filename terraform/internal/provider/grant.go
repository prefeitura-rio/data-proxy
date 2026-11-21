package provider

import (
	"context"
	"errors"
	"fmt"
	"regexp"
	"strings"

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
	_ resource.Resource                = (*grantResource)(nil)
	_ resource.ResourceWithConfigure   = (*grantResource)(nil)
	_ resource.ResourceWithImportState = (*grantResource)(nil)
)

// grantScope is the pattern of a scope: `*` for every project, or `project`, `project.schema` or
// `project.schema.table`. A table is a label of the ltree column.
var grantScope = regexp.MustCompile(`^(\*|[a-z][a-z0-9]{0,29}(\.[a-z][a-z0-9_]{0,55}(\.[A-Za-z0-9_-]{1,56})?)?)$`)

// grantResource is the resource data_proxy_grant. It adds and removes one row of access.policy.
type grantResource struct {
	client  *client.Client
	backoff client.Backoff
}

// grantModel is the Terraform state of a grant.
type grantModel struct {
	ID       types.String `tfsdk:"id"`
	Scope    types.String `tfsdk:"scope"`
	Subject  types.String `tfsdk:"subject"`
	UnitType types.String `tfsdk:"unit_type"`
	UnitID   types.String `tfsdk:"unit_id"`
}

// newGrantResource makes the resource data_proxy_grant.
func newGrantResource() resource.Resource {
	return &grantResource{backoff: client.DefaultBackoff}
}

// grantID joins the four columns of a grant into its ID.
func grantID(grant client.Grant) string {
	return strings.Join([]string{grant.Scope, grant.Subject, grant.UnitType, grant.UnitID}, "/")
}

// parseGrantID splits an import ID into a grant. The subject can hold slashes, because the ID ends with the two units.
func parseGrantID(id string) (client.Grant, error) {
	parts := strings.Split(id, "/")
	if len(parts) < 4 {
		return client.Grant{}, fmt.Errorf("the ID %q must be scope/subject/unit_type/unit_id", id)
	}

	last := len(parts) - 1
	grant := client.Grant{
		Scope:    parts[0],
		Subject:  strings.Join(parts[1:last-1], "/"),
		UnitType: parts[last-1],
		UnitID:   parts[last],
	}

	if !grantScope.MatchString(grant.Scope) || grant.Subject == "" || grant.UnitType == "" || grant.UnitID == "" {
		return client.Grant{}, fmt.Errorf("the ID %q needs a scope, a subject and the two units", id)
	}

	return grant, nil
}

// grantFromModel reads the grant that the state or the plan describes.
func grantFromModel(model grantModel) client.Grant {
	return client.Grant{
		Scope:    model.Scope.ValueString(),
		Subject:  model.Subject.ValueString(),
		UnitType: model.UnitType.ValueString(),
		UnitID:   model.UnitID.ValueString(),
	}
}

// Metadata names the resource with the type name of the provider and the suffix _grant.
func (gr *grantResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_grant"
}

// Schema declares the attributes of the grant.
func (gr *grantResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	replace := []planmodifier.String{stringplanmodifier.RequiresReplace()}
	notEmpty := []validator.String{stringvalidator.LengthAtLeast(1)}

	resp.Schema = schema.Schema{
		Description: "A grant of the access policy: the subject may read the rows of one unit, in a project, a schema or a table. " +
			"The unit `*` of type `*` is every row. Manage only static grants, such as the grants of service accounts, because an application " +
			"that registers its own grants would delete rows that it didn't write.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Description:   "The scope, the subject, the unit type and the unit ID joined with slashes.",
				Computed:      true,
				PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()},
			},
			"scope": schema.StringAttribute{
				Description: "What the grant covers: `*` for every project, `project`, `project.schema` or `project.schema.table`. " +
					"A grant covers its scope and what is below it. Changing it replaces the grant.",
				Required:      true,
				Validators:    []validator.String{stringvalidator.RegexMatches(grantScope, "must be `*`, a project, `project.schema` or `project.schema.table`")},
				PlanModifiers: replace,
			},
			"subject": schema.StringAttribute{
				Description:   "The subject of the token: the value of the claim of the schema. Changing it replaces the grant.",
				Required:      true,
				Validators:    notEmpty,
				PlanModifiers: replace,
			},
			"unit_type": schema.StringAttribute{
				Description:   "The type of the unit, or `*` with the unit `*` for every row. Changing it replaces the grant.",
				Required:      true,
				Validators:    notEmpty,
				PlanModifiers: replace,
			},
			"unit_id": schema.StringAttribute{
				Description:   "The unit that the grant covers. Changing it replaces the grant.",
				Required:      true,
				Validators:    notEmpty,
				PlanModifiers: replace,
			},
		},
	}
}

// Configure takes the client that the provider built.
func (gr *grantResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	gr.client = providerClient(req.ProviderData, &resp.Diagnostics)
}

// Create adds the row.
func (gr *grantResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan grantModel

	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)

	if resp.Diagnostics.HasError() {
		return
	}

	grant := grantFromModel(plan)

	err := gr.client.CreateGrant(ctx, grant)
	if err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't create the grant", "create_policy", err)

		return
	}

	plan.ID = types.StringValue(grantID(grant))
	resp.Diagnostics.Append(resp.State.Set(ctx, plan)...)
}

// Read checks that the row exists, and drops it from the state when it doesn't.
func (gr *grantResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state grantModel

	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)

	if resp.Diagnostics.HasError() {
		return
	}

	err := client.RetryNoRow(ctx, gr.backoff, func(ctx context.Context) error {
		rows, err := gr.client.ListGrants(ctx, grantFromModel(state))
		if err == nil && len(rows) == 0 {
			err = client.ErrNoRow
		}

		return err
	})

	switch {
	case errors.Is(err, client.ErrNoRow):
		resp.State.RemoveResource(ctx)
	case err != nil:
		addAPIError(&resp.Diagnostics, "Couldn't read the grant", "read_policy", err)
	default:
		resp.Diagnostics.Append(resp.State.Set(ctx, state)...)
	}
}

// Update never runs, because every attribute replaces the row.
func (gr *grantResource) Update(_ context.Context, _ resource.UpdateRequest, resp *resource.UpdateResponse) {
	resp.Diagnostics.AddError("A grant can't change", "Every attribute replaces the row. Report this bug to the developers of the provider.")
}

// Delete removes the row.
func (gr *grantResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state grantModel

	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)

	if resp.Diagnostics.HasError() {
		return
	}

	err := gr.client.DeleteGrant(ctx, grantFromModel(state))
	if err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't delete the grant", "delete_policy", err)
	}
}

// ImportState reads the row by an ID of the form scope/subject/unit_type/unit_id.
func (gr *grantResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	grant, err := parseGrantID(req.ID)
	if err != nil {
		resp.Diagnostics.AddError("Invalid import ID", err.Error())

		return
	}

	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), req.ID)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("scope"), grant.Scope)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("subject"), grant.Subject)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("unit_type"), grant.UnitType)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("unit_id"), grant.UnitID)...)
}
