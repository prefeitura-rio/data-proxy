package provider

import (
	"errors"
	"fmt"
	"slices"
	"strings"

	"github.com/hashicorp/terraform-plugin-framework/diag"

	"github.com/prefeitura-rio/data-proxy/terraform/internal/client"
)

// readConfig is the permission that every resource and data source needs. Configure checks it once, and the
// database checks the write permissions on each request.
const readConfig = "read_config"

// addAPIError adds an error for a failed request. When the database refused it, the detail names permission.
func addAPIError(diagnostics *diag.Diagnostics, summary, permission string, err error) {
	detail := err.Error()

	switch {
	case errors.Is(err, client.ErrForbidden) && permission != "":
		detail = fmt.Sprintf("The token lacks the %s permission, or the schema isn't in the claim of the token. "+
			"Give the client a role that has %s. The API answered: %s", permission, permission, err)
	case errors.Is(err, client.ErrForbidden):
		detail = "The database refused the request. The API answered: " + err.Error()
	case errors.Is(err, client.ErrUnauthorized):
		detail = "The API refused the token. Check that the API trusts the identity provider of token_url. " +
			"The API answered: " + err.Error()
	}

	diagnostics.AddError(summary, detail)
}

// checkReadConfig returns an error that lists the permissions of the token when it lacks read_config.
func checkReadConfig(clientID string, permissions []string) diag.Diagnostics {
	var diagnostics diag.Diagnostics

	if slices.Contains(permissions, readConfig) {
		return diagnostics
	}

	held := "none"
	if len(permissions) > 0 {
		held = strings.Join(permissions, ", ")
	}

	diagnostics.AddError("Missing "+readConfig+" permission",
		fmt.Sprintf("The token of the client %q lacks the %s permission, and the provider needs it to read the configuration. "+
			"Give the client a role that has %s, such as dp_console or dp_admin. The token has these permissions: %s.",
			clientID, readConfig, readConfig, held))

	return diagnostics
}
