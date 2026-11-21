package provider

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/diag"

	"terraform-provider-data-proxy/internal/client"
)

// deleteError returns the error of a DELETE that the server answers with the status and the body.
func deleteError(t *testing.T, status int, body string) error {
	t.Helper()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)

		if _, err := w.Write([]byte(body)); err != nil {
			t.Errorf("write the response: %v", err)
		}
	}))
	t.Cleanup(server.Close)

	apiClient, err := client.New(server.URL, staticToken("jwt-123"), server.Client())
	if err != nil {
		t.Fatalf("client.New: %v", err)
	}

	err = apiClient.Delete(context.Background(), client.ConsoleProfile, "schemas", []client.Filter{client.Eq("name", "alpha")}, nil)
	if err == nil {
		t.Fatalf("Delete: got no error")
	}

	return err
}

// TestAddAPIError checks the detail that addAPIError gives for each kind of error.
func TestAddAPIError(t *testing.T) {
	forbiddenErr := deleteError(t, http.StatusForbidden, `{"code":"42501","message":"permission denied for table schemas"}`)
	unauthorizedErr := deleteError(t, http.StatusUnauthorized, `{"code":"PGRST303","message":"JWT expired"}`)

	tests := []struct {
		name       string
		permission string
		err        error
		want       string
	}{
		{
			name:       "a refused write names its permission",
			permission: "create_config",
			err:        forbiddenErr,
			want: "The token lacks the create_config permission, or the schema isn't in the claim of the token. " +
				"Give the client a role that has create_config. The API answered: postgrest: HTTP 403 42501: permission denied for table schemas",
		},
		{
			name: "a refused request that needs no permission",
			err:  forbiddenErr,
			want: "The database refused the request. The API answered: postgrest: HTTP 403 42501: permission denied for table schemas",
		},
		{
			name:       "a refused token",
			permission: "edit_config",
			err:        unauthorizedErr,
			want:       "The API refused the token. Check that the API trusts the identity provider of token_url. The API answered: postgrest: HTTP 401 PGRST303: JWT expired",
		},
		{
			name:       "another error",
			permission: "delete_config",
			err:        errors.New("DELETE schemas: connection refused"),
			want:       "DELETE schemas: connection refused",
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			var diagnostics diag.Diagnostics

			addAPIError(&diagnostics, "Couldn't delete the schema", testCase.permission, testCase.err)

			want := []diagnostic{{summary: "Couldn't delete the schema", detail: testCase.want}}
			if got := diagnosticsOf(diagnostics); !reflect.DeepEqual(got, want) {
				t.Errorf("diagnostics = %+v, want %+v", got, want)
			}
		})
	}
}

// TestCheckReadConfig checks the error that checkReadConfig gives for each set of permissions.
func TestCheckReadConfig(t *testing.T) {
	tests := []struct {
		name        string
		permissions []string
		diags       []diagnostic
	}{
		{name: "read_config among others", permissions: []string{"read_api", "read_config", "create_config"}},
		{
			name:        "write permissions without read_config",
			permissions: []string{"create_config", "edit_config"},
			diags: []diagnostic{{
				summary: "Missing read_config permission",
				detail: `The token of the client "ci" lacks the read_config permission, and the provider needs it to read the configuration. ` +
					"Give the client a role that has read_config, such as dp_console or dp_admin. The token has these permissions: create_config, edit_config.",
			}},
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			if got := diagnosticsOf(checkReadConfig("ci", testCase.permissions)); !reflect.DeepEqual(got, testCase.diags) {
				t.Errorf("diagnostics = %+v, want %+v", got, testCase.diags)
			}
		})
	}
}
