package provider

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"testing"

	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
	"github.com/hashicorp/terraform-plugin-testing/terraform"

	"terraform-provider-data-proxy/internal/client"
)

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
