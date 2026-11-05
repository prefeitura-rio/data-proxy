package provider

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"regexp"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/providerserver"
	"github.com/hashicorp/terraform-plugin-go/tfprotov6"
	"github.com/hashicorp/terraform-plugin-testing/helper/acctest"
	"github.com/hashicorp/terraform-plugin-testing/helper/resource"

	"github.com/prefeitura-rio/data-proxy/terraform/internal/client"
)

// The helpers that the acceptance tests share. An acceptance test is a TestAcc function that calls resource.Test.
// It runs only when TF_ACC is set, against the API that the DATAPROXY_* variables name, and it skips without
// TF_ACC. The task tf:test:int starts the console stack and runs the acceptance tests against it.

// testAccNamePrefix starts every name that an acceptance test creates, so a row that a test left behind is easy
// to find.
const testAccNamePrefix = "tfacc"

// testAccProviderFactories serves the provider inside the test process, so Terraform needs no build of the
// provider binary.
var testAccProviderFactories = map[string]func() (tfprotov6.ProviderServer, error){
	TypeName: providerserver.NewProtocol6WithError(New("test")()),
}

// testAccPreCheck fails the test when a required DATAPROXY_* variable is empty. Give it to the PreCheck of each
// resource.TestCase.
func testAccPreCheck(t *testing.T) {
	t.Helper()

	for _, name := range []string{EnvEndpoint, EnvTokenURL, EnvClientID, EnvClientSecret} {
		if os.Getenv(name) == "" {
			t.Fatalf("set %s for the acceptance tests", name)
		}
	}
}

// testAccName returns a new name for an object that a test creates: testAccNamePrefix and 10 random lowercase
// letters and digits. Two tests or two runs don't share a name, and the name is valid for a project and for a
// schema.
func testAccName() string {
	return testAccNamePrefix + acctest.RandStringFromCharSet(10, acctest.CharSetAlphaNum)
}

// testAccClient returns a client of the API with the credentials of the DATAPROXY_* variables, as the provider
// reads them from an empty provider block.
func testAccClient() (*client.Client, error) {
	tokens, err := client.NewClientCredentialsSource(client.ClientCredentials{
		TokenURL:     os.Getenv(EnvTokenURL),
		ClientID:     os.Getenv(EnvClientID),
		ClientSecret: os.Getenv(EnvClientSecret),
		Scopes:       splitScopes(os.Getenv(EnvScopes)),
	}, nil)
	if err != nil {
		return nil, fmt.Errorf("build the token source: %w", err)
	}

	apiClient, err := client.New(os.Getenv(EnvEndpoint), tokens, nil)
	if err != nil {
		return nil, fmt.Errorf("build the client: %w", err)
	}

	return apiClient, nil
}

// testAccCleanup deletes the rows of table, in the console profile, that match every filter when the test ends.
// It also deletes them when a step fails before Terraform destroys them. A row that Terraform already destroyed
// is no error. The test runs its cleanups in reverse order, so register a parent, such as a project, before its
// children. Without TF_ACC it registers nothing, because the acceptance test skips.
func testAccCleanup(t testing.TB, table string, filters ...client.Filter) {
	t.Helper()

	if os.Getenv(resource.EnvTfAcc) == "" {
		return
	}

	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), client.DefaultTimeout)
		defer cancel()

		apiClient, err := testAccClient()
		if err != nil {
			t.Errorf("clean up %s: %v", table, err)

			return
		}

		if err := apiClient.Delete(ctx, client.ConsoleProfile, table, filters, nil); err != nil {
			t.Errorf("clean up %s: %v", table, err)
		}
	})
}

func TestTestAccName(t *testing.T) {
	// The checks of the names of console.projects and console.schemas, in console/migrations/00012_add_projects.sql.
	project := regexp.MustCompile(`^[a-z][a-z0-9]{0,29}$`)
	schema := regexp.MustCompile(`^[a-z][a-z0-9_]{0,55}$`)
	want := regexp.MustCompile(`^tfacc[a-z0-9]{10}$`)

	first := testAccName()

	for _, name := range []string{first, testAccName()} {
		if !want.MatchString(name) || !project.MatchString(name) || !schema.MatchString(name) {
			t.Errorf("testAccName() = %q, want tfacc and 10 lowercase letters and digits, a valid project and schema name", name)
		}
	}

	if second := testAccName(); second == first {
		t.Errorf("testAccName() returned %q twice", first)
	}
}

// cleanupRecorder is a testing.TB that keeps the cleanups and the errors of testAccCleanup, so a test can run
// them and read the errors.
type cleanupRecorder struct {
	testing.TB

	cleanups []func()
	errors   []string
}

func (r *cleanupRecorder) Helper() {}

func (r *cleanupRecorder) Cleanup(cleanup func()) {
	r.cleanups = append(r.cleanups, cleanup)
}

func (r *cleanupRecorder) Errorf(format string, args ...any) {
	r.errors = append(r.errors, fmt.Sprintf(format, args...))
}

func TestTestAccCleanup(t *testing.T) {
	tests := []struct {
		id          string
		acceptance  bool
		tokenStatus int
		status      int
		body        string
		requests    []apiRequest
		errors      func(api fakeAPI) []string
	}{
		{
			id:     "no cleanup without TF_ACC",
			status: http.StatusOK,
			body:   `[]`,
		},
		{
			id:         "a delete of the matching rows",
			acceptance: true,
			status:     http.StatusOK,
			body:       `[{"name":"tfaccabc"}]`,
			requests:   []apiRequest{{method: http.MethodDelete, uri: "/schemas?name=eq.tfaccabc", contentProfile: "console", authorization: "Bearer jwt-123"}},
		},
		{
			id:         "no row left to delete",
			acceptance: true,
			status:     http.StatusOK,
			body:       `[]`,
			requests:   []apiRequest{{method: http.MethodDelete, uri: "/schemas?name=eq.tfaccabc", contentProfile: "console", authorization: "Bearer jwt-123"}},
		},
		{
			id:         "the database refuses the delete",
			acceptance: true,
			status:     http.StatusForbidden,
			body:       `{"code":"42501","message":"permission denied for table schemas"}`,
			requests:   []apiRequest{{method: http.MethodDelete, uri: "/schemas?name=eq.tfaccabc", contentProfile: "console", authorization: "Bearer jwt-123"}},
			errors: func(fakeAPI) []string {
				return []string{"clean up schemas: postgrest: HTTP 403 42501: permission denied for table schemas"}
			},
		},
		{
			id:          "the identity provider refuses the secret",
			acceptance:  true,
			tokenStatus: http.StatusUnauthorized,
			errors: func(api fakeAPI) []string {
				return []string{"clean up schemas: get the token: fetch a token from " + api.tokenURL +
					`: oauth2: "unauthorized_client" "Invalid client or Invalid client credentials"`}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.id, func(t *testing.T) {
			tokenStatus := tt.tokenStatus
			if tokenStatus == 0 {
				tokenStatus = http.StatusOK
			}

			api := newFakeAPI(t, tokenStatus, tt.status, tt.body)

			acceptance := ""
			if tt.acceptance {
				acceptance = "1"
			}

			t.Setenv(resource.EnvTfAcc, acceptance)
			t.Setenv(EnvEndpoint, api.endpoint)
			t.Setenv(EnvTokenURL, api.tokenURL)
			t.Setenv(EnvClientID, "terraform")
			t.Setenv(EnvClientSecret, "secret-123")
			t.Setenv(EnvScopes, "")

			recorder := &cleanupRecorder{TB: t}

			testAccCleanup(recorder, "schemas", client.Eq("name", "tfaccabc"))

			wantCleanups := 0
			if tt.acceptance {
				wantCleanups = 1
			}

			if len(recorder.cleanups) != wantCleanups {
				t.Fatalf("testAccCleanup registered %d cleanups, want %d", len(recorder.cleanups), wantCleanups)
			}

			if len(*api.requests) != 0 {
				t.Fatalf("the API got %d requests before the cleanup, want none", len(*api.requests))
			}

			for _, cleanup := range recorder.cleanups {
				cleanup()
			}

			if !reflect.DeepEqual(*api.requests, tt.requests) {
				t.Errorf("requests = %+v, want %+v", *api.requests, tt.requests)
			}

			var wantErrors []string
			if tt.errors != nil {
				wantErrors = tt.errors(api)
			}

			if !reflect.DeepEqual(recorder.errors, wantErrors) {
				t.Errorf("errors = %q, want %q", recorder.errors, wantErrors)
			}
		})
	}
}
