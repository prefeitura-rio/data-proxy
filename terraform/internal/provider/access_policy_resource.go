package provider

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/hashicorp/terraform-plugin-framework-timeouts/resource/timeouts"
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
	_ resource.Resource                = (*accessPolicyResource)(nil)
	_ resource.ResourceWithConfigure   = (*accessPolicyResource)(nil)
	_ resource.ResourceWithImportState = (*accessPolicyResource)(nil)
)

// accessPolicyCreateTimeout is the time that a create waits for the schema to be provisioned.
const accessPolicyCreateTimeout = 5 * time.Minute

// accessPolicyResource is the data_proxy_access_policy resource. A row says that a subject, and optionally one unit,
// may read the data of a schema.
type accessPolicyResource struct {
	client  *client.Client
	backoff client.Backoff
}

// accessPolicyModel maps the data_proxy_access_policy resource.
type accessPolicyModel struct {
	ID       types.String   `tfsdk:"id"`
	Schema   types.String   `tfsdk:"schema"`
	Subject  types.String   `tfsdk:"subject"`
	UnitType types.String   `tfsdk:"unit_type"`
	UnitID   types.String   `tfsdk:"unit_id"`
	Timeouts timeouts.Value `tfsdk:"timeouts"`
}

// newAccessPolicyResource returns the data_proxy_access_policy resource.
func newAccessPolicyResource() resource.Resource {
	return &accessPolicyResource{backoff: client.DefaultBackoff}
}

// accessPolicyID joins the schema, the subject and the units with slashes. A missing unit is an empty part.
func accessPolicyID(schemaName string, policy client.AccessPolicy) string {
	return strings.Join([]string{schemaName, policy.Subject, derefString(policy.UnitType), derefString(policy.UnitID)}, "/")
}

// derefString returns the text, or an empty text for nil.
func derefString(text *string) string {
	if text == nil {
		return ""
	}

	return *text
}

// emptyToNil returns nil for an empty text, and a pointer to the text otherwise.
func emptyToNil(text string) *string {
	if text == "" {
		return nil
	}

	return &text
}

// parseAccessPolicyID splits an import ID into the schema, the subject and the units. The subject can hold slashes,
// because the ID ends with the two units.
func parseAccessPolicyID(id string) (string, client.AccessPolicy, error) {
	parts := strings.Split(id, "/")
	if len(parts) < 4 {
		return "", client.AccessPolicy{}, fmt.Errorf("the ID %q must be schema/subject/unit_type/unit_id, with an empty unit for none", id)
	}

	last := len(parts) - 1
	policy := client.AccessPolicy{
		Subject:  strings.Join(parts[1:last-1], "/"),
		UnitType: emptyToNil(parts[last-1]),
		UnitID:   emptyToNil(parts[last]),
	}

	if parts[0] == "" || policy.Subject == "" {
		return "", client.AccessPolicy{}, fmt.Errorf("the ID %q needs a schema and a subject", id)
	}

	return parts[0], policy, nil
}

// policyOf returns the row that the model describes.
func policyOf(model accessPolicyModel) client.AccessPolicy {
	return client.AccessPolicy{
		Subject:  model.Subject.ValueString(),
		UnitType: stringPointer(model.UnitType),
		UnitID:   stringPointer(model.UnitID),
	}
}

// Metadata names the resource with the type name of the provider and the suffix _access_policy.
func (r *accessPolicyResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_access_policy"
}

// Schema declares the attributes of the access policy.
func (r *accessPolicyResource) Schema(ctx context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	replace := []planmodifier.String{stringplanmodifier.RequiresReplace()}
	notEmpty := []validator.String{stringvalidator.LengthAtLeast(1)}

	resp.Schema = schema.Schema{
		Description: "A row of the access policy of a schema: the subject may read the data, limited to one unit when the row names it. " +
			"Manage only static grants, such as the grants of service accounts, because an application that registers its own policies would delete rows that it didn't write.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Description:   "The schema, the subject, the unit type and the unit ID joined with slashes.",
				Computed:      true,
				PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()},
			},
			"schema": schema.StringAttribute{
				Description:   "The physical name of the schema, such as `data_proxy_schema.example.name`. Changing it replaces the row.",
				Required:      true,
				Validators:    notEmpty,
				PlanModifiers: replace,
			},
			"subject": schema.StringAttribute{
				Description:   "The subject of the token: the value of the claim of the schema. Changing it replaces the row.",
				Required:      true,
				Validators:    notEmpty,
				PlanModifiers: replace,
			},
			"unit_type": schema.StringAttribute{
				Description:   "The type of the unit that limits the grant, or none for the whole schema. Changing it replaces the row.",
				Optional:      true,
				Validators:    notEmpty,
				PlanModifiers: replace,
			},
			"unit_id": schema.StringAttribute{
				Description:   "The unit that limits the grant. Changing it replaces the row.",
				Optional:      true,
				Validators:    notEmpty,
				PlanModifiers: replace,
			},
		},
		Blocks: map[string]schema.Block{
			"timeouts": timeouts.Block(ctx, timeouts.Opts{Create: true}),
		},
	}
}

// Configure takes the client that the provider built.
func (r *accessPolicyResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.client = providerClient(req.ProviderData, &resp.Diagnostics)
}

// Create adds the row, and tries again while the schema has no access_policy table yet, within the create timeout.
func (r *accessPolicyResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan accessPolicyModel

	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)

	limit, diagnostics := plan.Timeouts.Create(ctx, accessPolicyCreateTimeout)
	resp.Diagnostics.Append(diagnostics...)

	if resp.Diagnostics.HasError() {
		return
	}

	waitCtx, cancel := context.WithTimeout(ctx, limit)
	defer cancel()

	policy := policyOf(plan)

	if err := r.client.CreateAccessPolicyWhenProvisioned(waitCtx, plan.Schema.ValueString(), policy); err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't create the access policy", "create_policy", err)

		return
	}

	plan.ID = types.StringValue(accessPolicyID(plan.Schema.ValueString(), policy))
	resp.Diagnostics.Append(resp.State.Set(ctx, plan)...)
}

// Read checks that the row exists, and drops it from the state when it doesn't. More than one equal row is an error,
// because the table has no primary key.
func (r *accessPolicyResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state accessPolicyModel

	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)

	if resp.Diagnostics.HasError() {
		return
	}

	var rows []client.AccessPolicy

	err := client.RetryNoRow(ctx, r.backoff, func(ctx context.Context) (err error) {
		rows, err = r.client.ListAccessPolicy(ctx, state.Schema.ValueString(), policyOf(state))
		if err == nil && len(rows) == 0 {
			err = client.ErrNoRow
		}

		return err
	})

	switch {
	case errors.Is(err, client.ErrNoRow):
		resp.State.RemoveResource(ctx)
	case err != nil:
		addAPIError(&resp.Diagnostics, "Couldn't read the access policy", "read_policy", err)
	case len(rows) > 1:
		resp.Diagnostics.AddError("The access policy has duplicate rows",
			fmt.Sprintf("The table of the schema %s has %d equal rows for %s. The table has no primary key, so the database allows them. Delete the extra rows by hand.",
				state.Schema.ValueString(), len(rows), state.ID.ValueString()))
	default:
		resp.Diagnostics.Append(resp.State.Set(ctx, state)...)
	}
}

// Update never runs, because every attribute replaces the row.
func (r *accessPolicyResource) Update(_ context.Context, _ resource.UpdateRequest, resp *resource.UpdateResponse) {
	resp.Diagnostics.AddError("An access policy can't change", "Every attribute replaces the row. Report this bug to the developers of the provider.")
}

// Delete removes the row.
func (r *accessPolicyResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state accessPolicyModel

	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)

	if resp.Diagnostics.HasError() {
		return
	}

	if err := r.client.DeleteAccessPolicy(ctx, state.Schema.ValueString(), policyOf(state)); err != nil {
		addAPIError(&resp.Diagnostics, "Couldn't delete the access policy", "delete_policy", err)
	}
}

// ImportState reads the row by an ID of the form schema/subject/unit_type/unit_id, with an empty part for a missing
// unit.
func (r *accessPolicyResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	schemaName, policy, err := parseAccessPolicyID(req.ID)
	if err != nil {
		resp.Diagnostics.AddError("Invalid import ID", err.Error())

		return
	}

	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), req.ID)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("schema"), schemaName)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("subject"), policy.Subject)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("unit_type"), stringValue(policy.UnitType))...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("unit_id"), stringValue(policy.UnitID))...)
}
