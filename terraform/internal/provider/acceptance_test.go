package provider

import (
	"context"
	"encoding/json"
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
	"github.com/hashicorp/terraform-plugin-testing/terraform"

	"terraform-provider-data-proxy/internal/client"
)

// testAccNamePrefix starts every name that an acceptance test creates, so a row that a test left behind is easy to find.
const testAccNamePrefix = "tfacc"

// testAccProviderName is the local name of the provider in the test configurations. A local name has no underscore,
// so it differs from TypeName, and each resource of a test names it with provider = data-proxy.
const testAccProviderName = "data-proxy"

// testAccProviderFactories serves the provider inside the test process, so Terraform needs no provider binary.
var testAccProviderFactories = map[string]func() (tfprotov6.ProviderServer, error){
	testAccProviderName: providerserver.NewProtocol6WithError(New("test")()),
}

// testAccPreCheck fails the test when a required DATA_PROXY_* variable is empty.
func testAccPreCheck(t *testing.T) {
	t.Helper()

	for _, name := range []string{EnvEndpoint, EnvTokenURL, EnvClientID, EnvClientSecret} {
		if os.Getenv(name) == "" {
			t.Fatalf("set %s for the acceptance tests", name)
		}
	}
}

// testAccName returns a new name for an object that a test creates. The name is valid for a project and for a schema.
func testAccName() string {
	return testAccNamePrefix + acctest.RandStringFromCharSet(10, acctest.CharSetAlphaNum)
}

// testAccClient returns a client with the credentials of the DATA_PROXY_* variables.
func testAccClient() (*client.Client, error) {
	tokens, err := client.NewCredentialsSource(client.Credentials{
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

// testAccCleanup deletes the rows of table that match every filter when the test ends. Cleanups run in reverse
// order, so register a parent, such as a project, before its children.
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

// TestTestAccName checks that each name fits the name rules of the database and that two names differ.
func TestTestAccName(t *testing.T) {
	// These patterns copy the name checks of console.projects and console.schemas in migration 00012.
	tests := []struct {
		name    string
		pattern *regexp.Regexp
	}{
		{name: "the shape of a test name", pattern: regexp.MustCompile(`^tfacc[a-z0-9]{10}$`)},
		{name: "the name of a project", pattern: regexp.MustCompile(`^[a-z][a-z0-9]{0,29}$`)},
		{name: "the name of a schema", pattern: regexp.MustCompile(`^[a-z][a-z0-9_]{0,55}$`)},
	}

	first, second := testAccName(), testAccName()

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			for _, name := range []string{first, second} {
				if !tt.pattern.MatchString(name) {
					t.Errorf("testAccName() = %q, want a match of %s", name, tt.pattern)
				}
			}
		})
	}

	t.Run("two names differ", func(t *testing.T) {
		if first == second {
			t.Errorf("testAccName() returned %q twice", first)
		}
	})
}

// cleanupRecorder is a testing.TB that keeps the cleanups and the errors of testAccCleanup.
type cleanupRecorder struct {
	testing.TB

	cleanups []func()
	errors   []string
}

// Helper does nothing.
func (r *cleanupRecorder) Helper() {}

// Cleanup keeps the cleanup so that the test can run it.
func (r *cleanupRecorder) Cleanup(cleanup func()) {
	r.cleanups = append(r.cleanups, cleanup)
}

// Errorf keeps the error so that the test can read it.
func (r *cleanupRecorder) Errorf(format string, args ...any) {
	r.errors = append(r.errors, fmt.Sprintf(format, args...))
}

func TestTestAccCleanup(t *testing.T) {
	tests := []struct {
		name        string
		acceptance  bool
		tokenStatus int
		status      int
		body        string
		requests    []apiRequest
		errors      func(api fakeAPI) []string
	}{
		{
			name:   "no cleanup without TF_ACC",
			status: http.StatusOK,
			body:   `[]`,
		},
		{
			name:       "a delete of the matching rows",
			acceptance: true,
			status:     http.StatusOK,
			body:       `[{"name":"tfaccabc"}]`,
			requests:   []apiRequest{{method: http.MethodDelete, uri: "/schemas?name=eq.tfaccabc", contentProfile: "console", authorization: "Bearer jwt-123"}},
		},
		{
			name:       "no row left to delete",
			acceptance: true,
			status:     http.StatusOK,
			body:       `[]`,
			requests:   []apiRequest{{method: http.MethodDelete, uri: "/schemas?name=eq.tfaccabc", contentProfile: "console", authorization: "Bearer jwt-123"}},
		},
		{
			name:       "the database refuses the delete",
			acceptance: true,
			status:     http.StatusForbidden,
			body:       `{"code":"42501","message":"permission denied for table schemas"}`,
			requests:   []apiRequest{{method: http.MethodDelete, uri: "/schemas?name=eq.tfaccabc", contentProfile: "console", authorization: "Bearer jwt-123"}},
			errors: func(fakeAPI) []string {
				return []string{"clean up schemas: postgrest: HTTP 403 42501: permission denied for table schemas"}
			},
		},
		{
			name:        "the identity provider refuses the secret",
			acceptance:  true,
			tokenStatus: http.StatusUnauthorized,
			errors: func(api fakeAPI) []string {
				return []string{"clean up schemas: get the token: fetch a token from " + api.tokenURL +
					`: oauth2: "unauthorized_client" "Invalid client or Invalid client credentials"`}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
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

// testAccProvisionedSchema is a schema that the console stack provisions at start, so its access policy table exists.
const testAccProvisionedSchema = "test"

// projectConfig returns the configuration of a project.
func projectConfig(name string) string {
	return fmt.Sprintf(`
resource "data_proxy_project" "this" {
  provider = data-proxy
  name     = %q
}
`, name)
}

// schemaConfig returns the configuration of a project and a schema in it. The timeouts of 0s skip the wait for the
// sync, which the console stack doesn't run.
func schemaConfig(project, shortName, claim string) string {
	return projectConfig(project) + fmt.Sprintf(`
resource "data_proxy_schema" "this" {
  provider            = data-proxy
  project             = data_proxy_project.this.name
  short_name          = %q
  claim               = %q
  deletion_protection = false

  timeouts {
    create = "0s"
    delete = "0s"
  }
}
`, shortName, claim)
}

func TestAccProjectResource(t *testing.T) {
	name := testAccName()
	testAccCleanup(t, "projects", client.Eq("name", name))

	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories,
		Steps: []resource.TestStep{
			{
				Config: projectConfig(name),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("data_proxy_project.this", "id", name),
					resource.TestCheckResourceAttrSet("data_proxy_project.this", "updated_by"),
				),
			},
			{ResourceName: "data_proxy_project.this", ImportState: true, ImportStateVerify: true},
		},
	})
}

func TestAccProjectResourceRefusesTheDefaultName(t *testing.T) {
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories,
		Steps: []resource.TestStep{
			{Config: projectConfig("default"), ExpectError: regexp.MustCompile(`value must be none of`)},
		},
	})
}

func TestAccProjectResourceRefusesToDeleteAProjectWithSchemas(t *testing.T) {
	name := testAccName()
	testAccCleanup(t, "projects", client.Eq("name", name))
	testAccCleanup(t, "schemas", client.Eq("project", name))

	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories,
		Steps: []resource.TestStep{
			{Config: projectConfig(name)},
			{
				PreConfig: func() {
					apiClient, err := testAccClient()
					if err != nil {
						t.Fatalf("testAccClient: %v", err)
					}

					if _, err := apiClient.CreateSchema(context.Background(), client.SchemaInput{Project: name, ShortName: "orders"}); err != nil {
						t.Fatalf("add a schema outside Terraform: %v", err)
					}
				},
				Config:      `# the project leaves the configuration`,
				ExpectError: regexp.MustCompile(`Couldn't delete the project`),
			},
			{
				PreConfig: func() {
					apiClient, err := testAccClient()
					if err != nil {
						t.Fatalf("testAccClient: %v", err)
					}

					if err := apiClient.DeleteSchema(context.Background(), name+"_orders"); err != nil {
						t.Fatalf("delete the schema outside Terraform: %v", err)
					}
				},
				Config: projectConfig(name),
			},
		},
	})
}

func TestAccSchemaResource(t *testing.T) {
	project := testAccName()
	testAccCleanup(t, "projects", client.Eq("name", project))
	testAccCleanup(t, "schemas", client.Eq("project", project))

	physical := project + "_orders"

	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories,
		Steps: []resource.TestStep{
			{
				Config: schemaConfig(project, "orders", "preferred_username"),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("data_proxy_schema.this", "name", physical),
					resource.TestCheckResourceAttr("data_proxy_schema.this", "id", physical),
					resource.TestCheckResourceAttr("data_proxy_schema.this", "source", "bigquery"),
					resource.TestCheckResourceAttr("data_proxy_schema.this", "claim", "preferred_username"),
				),
			},
			{
				Config: schemaConfig(project, "orders", "email"),
				Check:  resource.TestCheckResourceAttr("data_proxy_schema.this", "claim", "email"),
			},
			{
				ResourceName:            "data_proxy_schema.this",
				ImportState:             true,
				ImportStateVerify:       true,
				ImportStateVerifyIgnore: []string{"deletion_protection", "timeouts"},
			},
			{
				Config: schemaConfig(project, "invoices", "email"),
				Check:  resource.TestCheckResourceAttr("data_proxy_schema.this", "name", project+"_invoices"),
			},
		},
	})
}

func TestAccSchemaResourceInTheDefaultProject(t *testing.T) {
	short := testAccName()
	testAccCleanup(t, "schemas", client.Eq("name", short))

	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories,
		Steps: []resource.TestStep{
			{
				Config: fmt.Sprintf(`
resource "data_proxy_schema" "this" {
  provider            = data-proxy
  short_name          = %q
  deletion_protection = false

  timeouts {
    create = "0s"
    delete = "0s"
  }
}
`, short),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("data_proxy_schema.this", "project", "default"),
					resource.TestCheckResourceAttr("data_proxy_schema.this", "name", short),
				),
			},
		},
	})
}

func TestAccSchemaResourceRefusesAReservedName(t *testing.T) {
	testAccCleanup(t, "schemas", client.Eq("name", "dbos"))

	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories,
		Steps: []resource.TestStep{
			{
				Config: `
resource "data_proxy_schema" "this" {
  provider   = data-proxy
  short_name = "dbos"

  timeouts {
    create = "0s"
  }
}
`,
				ExpectError: regexp.MustCompile(`Couldn't create the schema`),
			},
		},
	})
}

// tableAccConfig returns a schema with a claim and a table in it, with the row policies that the caller gives.
func tableAccConfig(project, table string, cacheTTL int, rules ...[2]string) string {
	blocks := ""
	for _, rule := range rules {
		blocks += fmt.Sprintf(`
  rls {
    column_name = %q
    unit_type   = %q
  }
`, rule[0], rule[1])
	}

	return schemaConfig(project, "orders", "preferred_username") + fmt.Sprintf(`
resource "data_proxy_table" "this" {
  provider  = data-proxy
  name      = %q
  schema    = data_proxy_schema.this.name
  cache_ttl = %d
  sort      = ["order_id"]
%s}
`, table, cacheTTL, blocks)
}

func TestAccTableResource(t *testing.T) {
	project := testAccName()
	table := "p.d." + testAccName()

	testAccCleanup(t, "projects", client.Eq("name", project))
	testAccCleanup(t, "schemas", client.Eq("project", project))
	testAccCleanup(t, "tables", client.Eq("name", table))

	unit, region := [2]string{"unit_id", "unit"}, [2]string{"region_id", "region"}

	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories,
		Steps: []resource.TestStep{
			{
				Config: tableAccConfig(project, table, 60, unit, region),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("data_proxy_table.this", "strategy", "full"),
					resource.TestCheckResourceAttr("data_proxy_table.this", "cache_ttl", "60"),
					resource.TestCheckResourceAttr("data_proxy_table.this", "rls.#", "2"),
					resource.TestCheckResourceAttr("data_proxy_table.this", "rls.0.column_name", "unit_id"),
					resource.TestCheckResourceAttr("data_proxy_table.this", "rls.1.unit_type", "region"),
				),
			},
			{
				Config: tableAccConfig(project, table, 120, region, unit),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("data_proxy_table.this", "cache_ttl", "120"),
					resource.TestCheckResourceAttr("data_proxy_table.this", "rls.0.column_name", "region_id"),
					resource.TestCheckResourceAttr("data_proxy_table.this", "rls.1.column_name", "unit_id"),
				),
			},
			{ResourceName: "data_proxy_table.this", ImportState: true, ImportStateVerify: true},
			{
				Config: tableAccConfig(project, table, 120),
				Check:  resource.TestCheckResourceAttr("data_proxy_table.this", "rls.#", "0"),
			},
		},
	})
}

func TestAccTableResourceKeepsAGroupOfConditionsInOneBlock(t *testing.T) {
	project := testAccName()
	table := "p.d." + testAccName()

	testAccCleanup(t, "projects", client.Eq("name", project))
	testAccCleanup(t, "schemas", client.Eq("project", project))
	testAccCleanup(t, "tables", client.Eq("name", table))

	config := schemaConfig(project, "orders", "preferred_username") + fmt.Sprintf(`
resource "data_proxy_table" "this" {
  provider = data-proxy
  name     = %q
  schema   = data_proxy_schema.this.name

  rls {
    condition {
      column_name = "region_id"
      unit_type   = "region"
    }
    condition {
      column_name = "unit_id"
      unit_type   = "unit"
    }
  }

  rls {
    column_name = "area"
    unit_type   = "area"
  }
}
`, table)

	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories,
		Steps: []resource.TestStep{
			{
				Config: config,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("data_proxy_table.this", "rls.#", "2"),
					resource.TestCheckResourceAttr("data_proxy_table.this", "rls.0.condition.#", "2"),
					resource.TestCheckResourceAttr("data_proxy_table.this", "rls.0.condition.1.column_name", "unit_id"),
					resource.TestCheckResourceAttr("data_proxy_table.this", "rls.1.column_name", "area"),
				),
			},
			{ResourceName: "data_proxy_table.this", ImportState: true, ImportStateVerify: true},
		},
	})
}

func TestAccTableResourceRefusesARowPolicyWithoutAClaim(t *testing.T) {
	project := testAccName()
	table := "p.d." + testAccName()

	testAccCleanup(t, "projects", client.Eq("name", project))
	testAccCleanup(t, "schemas", client.Eq("project", project))
	testAccCleanup(t, "tables", client.Eq("name", table))

	config := projectConfig(project) + fmt.Sprintf(`
resource "data_proxy_schema" "this" {
  provider            = data-proxy
  project             = data_proxy_project.this.name
  short_name          = "orders"
  deletion_protection = false

  timeouts {
    create = "0s"
    delete = "0s"
  }
}

resource "data_proxy_table" "this" {
  provider = data-proxy
  name     = %q
  schema   = data_proxy_schema.this.name

  rls {
    column_name = "unit_id"
    unit_type   = "unit"
  }
}
`, table)

	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories,
		Steps: []resource.TestStep{
			{Config: config, ExpectError: regexp.MustCompile(`Couldn't add the row policies of the table`)},
		},
	})
}

func TestAccTableResourceRefusesNWithAFullStrategy(t *testing.T) {
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories,
		Steps: []resource.TestStep{
			{
				Config: `
resource "data_proxy_table" "this" {
  provider = data-proxy
  name     = "p.d.t"
  schema   = "s"
  n        = 3
}
`,
				ExpectError: regexp.MustCompile(`n needs the strategy`),
			},
		},
	})
}

func TestAccTableResourceRefusesAnRLSBlockThatMixesBothForms(t *testing.T) {
	tests := []struct {
		name  string
		block string
		want  string
	}{
		{
			name: "both forms",
			block: `
  rls {
    column_name = "a"
    unit_type   = "x"
    condition {
      column_name = "b"
      unit_type   = "y"
    }
    condition {
      column_name = "c"
      unit_type   = "z"
    }
  }`,
			want: `both a column and conditions`,
		},
		{
			name: "one condition block",
			block: `
  rls {
    condition {
      column_name = "a"
      unit_type   = "x"
    }
  }`,
			want: `written flat`,
		},
		{name: "an empty block", block: `
  rls {}`, want: `needs column_name and unit_type`},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			resource.Test(t, resource.TestCase{
				PreCheck:                 func() { testAccPreCheck(t) },
				ProtoV6ProviderFactories: testAccProviderFactories,
				Steps: []resource.TestStep{
					{
						Config: `
resource "data_proxy_table" "this" {
  provider = data-proxy
  name     = "p.d.t"
  schema   = "s"
` + test.block + `
}
`,
						ExpectError: regexp.MustCompile(test.want),
					},
				},
			})
		})
	}
}

func TestAccAccessPolicyResource(t *testing.T) {
	subject := testAccName() + "@example.org"

	config := fmt.Sprintf(`
resource "data_proxy_access_policy" "this" {
  provider  = data-proxy
  schema    = %q
  subject   = %q
  unit_type = "unit"
  unit_id   = "1"
}
`, testAccProvisionedSchema, subject)

	destroyed := func(*terraform.State) error {
		apiClient, err := testAccClient()
		if err != nil {
			return err
		}

		rows, err := apiClient.ListAccessPolicy(context.Background(), testAccProvisionedSchema, client.AccessPolicy{Subject: subject, UnitType: ptr("unit"), UnitID: ptr("1")})
		if err != nil {
			return err
		}

		if len(rows) != 0 {
			return fmt.Errorf("the row of %s still exists after the destroy", subject)
		}

		return nil
	}

	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories,
		CheckDestroy:             destroyed,
		Steps: []resource.TestStep{
			{
				Config: config,
				Check:  resource.TestCheckResourceAttr("data_proxy_access_policy.this", "id", fmt.Sprintf("%s/%s/unit/1", testAccProvisionedSchema, subject)),
			},
			{ResourceName: "data_proxy_access_policy.this", ImportState: true, ImportStateVerify: true, ImportStateVerifyIdentifierAttribute: "id"},
		},
	})
}

func TestAccDataSources(t *testing.T) {
	project := testAccName()
	table := "p.d." + testAccName()

	testAccCleanup(t, "projects", client.Eq("name", project))
	testAccCleanup(t, "schemas", client.Eq("project", project))
	testAccCleanup(t, "tables", client.Eq("name", table))

	config := tableAccConfig(project, table, 30, [2]string{"unit_id", "unit"}) + fmt.Sprintf(`
data "data_proxy_project" "this" {
  provider = data-proxy
  name     = data_proxy_project.this.name
}

data "data_proxy_schema" "by_parts" {
  provider   = data-proxy
  project    = data_proxy_project.this.name
  short_name = "orders"

  depends_on = [data_proxy_schema.this]
}

data "data_proxy_table" "this" {
  provider = data-proxy
  name     = data_proxy_table.this.name
}

data "data_proxy_export_config" "this" {
  provider   = data-proxy
  depends_on = [data_proxy_table.this]
}

data "data_proxy_config_history" "this" {
  provider   = data-proxy
  schema     = data_proxy_schema.this.name
  limit      = 5
  depends_on = [data_proxy_table.this]
}

data "data_proxy_schema_health" "provisioned" {
  provider = data-proxy
  name     = %q
}
`, testAccProvisionedSchema)

	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProviderFactories,
		Steps: []resource.TestStep{
			{
				Config: config,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("data.data_proxy_project.this", "id", project),
					resource.TestCheckResourceAttr("data.data_proxy_schema.by_parts", "name", project+"_orders"),
					resource.TestCheckResourceAttr("data.data_proxy_schema.by_parts", "claim", "preferred_username"),
					resource.TestCheckResourceAttr("data.data_proxy_table.this", "cache_ttl", "30"),
					resource.TestCheckResourceAttr("data.data_proxy_table.this", "rls.0.unit_type", "unit"),
					resource.TestCheckResourceAttr("data.data_proxy_schema_health.provisioned", "schema_exists", "true"),
					resource.TestCheckResourceAttrWith("data.data_proxy_config_history.this", "entries.#", func(value string) error {
						if value == "0" {
							return fmt.Errorf("the history of %s has no entry", project)
						}

						return nil
					}),
					resource.TestCheckResourceAttrWith("data.data_proxy_export_config.this", "document", func(value string) error {
						var document struct {
							Projects map[string]json.RawMessage `json:"projects"`
						}

						if err := json.Unmarshal([]byte(value), &document); err != nil {
							return err
						}

						if _, ok := document.Projects[project]; !ok {
							return fmt.Errorf("the export has no project %s", project)
						}

						return nil
					}),
				),
			},
		},
	})
}
