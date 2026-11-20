package client

import (
	"context"
	"encoding/json"
	"fmt"
)

// Project is a row of console.projects.
type Project struct {
	Name      string `json:"name"`
	UpdatedAt string `json:"updated_at"`
	UpdatedBy string `json:"updated_by"`
}

// Schema is a row of console.schemas. Name is the physical name that the database builds from Project and ShortName.
type Schema struct {
	Name      string          `json:"name"`
	Project   string          `json:"project"`
	ShortName string          `json:"short_name"`
	Source    string          `json:"source"`
	Settings  json.RawMessage `json:"settings"`
	Claim     *string         `json:"claim"`
	Encrypted bool            `json:"encrypted"`
	UpdatedAt string          `json:"updated_at"`
	UpdatedBy string          `json:"updated_by"`
}

// SchemaInput is what a new schema needs. An empty Source means the default of the database, and nil Settings and
// Claim leave the columns out.
type SchemaInput struct {
	Project   string          `json:"project"`
	ShortName string          `json:"short_name"`
	Source    string          `json:"source,omitempty"`
	Settings  json.RawMessage `json:"settings,omitempty"`
	Claim     *string         `json:"claim,omitempty"`
	Encrypted bool            `json:"encrypted"`
}

// SchemaUpdate holds the columns of a schema that can change. A nil Settings or Claim clears the column.
type SchemaUpdate struct {
	Source    string          `json:"source"`
	Settings  json.RawMessage `json:"settings"`
	Claim     *string         `json:"claim"`
	Encrypted bool            `json:"encrypted"`
}

// Table is a row of console.tables.
type Table struct {
	Name         string          `json:"name"`
	Schema       string          `json:"schema"`
	Strategy     string          `json:"strategy"`
	N            *int            `json:"n"`
	Fallback     bool            `json:"fallback"`
	CacheTTL     *int            `json:"cache_ttl"`
	Partitioning json.RawMessage `json:"partitioning"`
	Sort         []string        `json:"sort"`
	UpdatedAt    string          `json:"updated_at"`
	UpdatedBy    string          `json:"updated_by"`
}

// TableInput is what a new table needs. A nil optional field leaves the column out, so the database default applies.
type TableInput struct {
	Name         string          `json:"name"`
	Schema       string          `json:"schema"`
	Strategy     string          `json:"strategy,omitempty"`
	N            *int            `json:"n,omitempty"`
	Fallback     bool            `json:"fallback"`
	CacheTTL     *int            `json:"cache_ttl,omitempty"`
	Partitioning json.RawMessage `json:"partitioning,omitempty"`
	Sort         []string        `json:"sort,omitempty"`
}

// TableUpdate holds the columns of a table that can change. A nil field clears the column.
type TableUpdate struct {
	Strategy     string          `json:"strategy"`
	N            *int            `json:"n"`
	Fallback     bool            `json:"fallback"`
	CacheTTL     *int            `json:"cache_ttl"`
	Partitioning json.RawMessage `json:"partitioning"`
	Sort         []string        `json:"sort"`
}

// RLSCondition is one condition: the column that holds the unit and the type of the unit.
type RLSCondition struct {
	ColumnName string `json:"column"`
	UnitType   string `json:"unit_type"`
}

// RLSRule is a row policy of a table: one condition or more, and every one must match. A rule of two conditions or
// more is a group. No number of a group exists: the database numbers the groups by their position.
type RLSRule struct {
	Conditions []RLSCondition `json:"conditions"`
}

// rulesRow is a row of console.rules(): a table and its groups in order.
type rulesRow struct {
	TableName string    `json:"table_name"`
	Groups    []RLSRule `json:"groups"`
}

// Health is a row of the view console.health.
type Health struct {
	Name         string `json:"name"`
	SchemaExists bool   `json:"schema_exists"`
	AccessPolicy bool   `json:"access_policy"`
	ViewCount    int    `json:"view_count"`
}

// TableSyncState is a row of the view console.synced.
type TableSyncState struct {
	Schema         string  `json:"schema"`
	Name           string  `json:"name"`
	Synced         bool    `json:"synced"`
	Signature      *string `json:"signature"`
	PartitionCount int     `json:"partition_count"`
}

// HistoryEntry is a row of console.config_history.
type HistoryEntry struct {
	ChangedAt  string          `json:"changed_at"`
	ChangedBy  *string         `json:"changed_by"`
	Action     string          `json:"action"`
	Object     string          `json:"object"`
	SchemaName string          `json:"schema_name"`
	Key        string          `json:"key"`
	OldRow     json.RawMessage `json:"old_row"`
	NewRow     json.RawMessage `json:"new_row"`
}

// onlyRow returns the one row that a write gave back, or an error when the answer has another count.
func onlyRow[T any](rows []T, table string) (T, error) {
	var zero T

	if len(rows) != 1 {
		return zero, fmt.Errorf("the write to %s gave back %d rows, want one", table, len(rows))
	}

	return rows[0], nil
}

// CreateProject adds the project and returns its row.
func (c *Client) CreateProject(ctx context.Context, name string) (Project, error) {
	var rows []Project

	if err := c.Insert(ctx, ConsoleProfile, "projects", map[string]string{"name": name}, &rows); err != nil {
		return Project{}, err
	}

	return onlyRow(rows, "projects")
}

// GetProject reads the project. It wraps ErrNoRow when the project doesn't exist.
func (c *Client) GetProject(ctx context.Context, name string) (Project, error) {
	var project Project

	err := c.GetOne(ctx, ConsoleProfile, "projects", []Filter{Eq("name", name)}, &project)

	return project, err
}

// DeleteProject removes the project. The database refuses it while the project has schemas.
func (c *Client) DeleteProject(ctx context.Context, name string) error {
	return c.Delete(ctx, ConsoleProfile, "projects", []Filter{Eq("name", name)}, nil)
}

// CreateSchema adds the schema and returns its row, with the physical name that the database built.
func (c *Client) CreateSchema(ctx context.Context, input SchemaInput) (Schema, error) {
	var rows []Schema

	if err := c.Insert(ctx, ConsoleProfile, "schemas", input, &rows); err != nil {
		return Schema{}, err
	}

	return onlyRow(rows, "schemas")
}

// GetSchema reads the schema by its physical name. It wraps ErrNoRow when the schema doesn't exist.
func (c *Client) GetSchema(ctx context.Context, name string) (Schema, error) {
	var schema Schema

	err := c.GetOne(ctx, ConsoleProfile, "schemas", []Filter{Eq("name", name)}, &schema)

	return schema, err
}

// GetSchemaByParts reads the schema by its project and its short name.
func (c *Client) GetSchemaByParts(ctx context.Context, project, shortName string) (Schema, error) {
	var schema Schema

	filters := []Filter{Eq("project", project), Eq("short_name", shortName)}
	err := c.GetOne(ctx, ConsoleProfile, "schemas", filters, &schema)

	return schema, err
}

// UpdateSchema changes the columns that a schema allows to change and returns its row.
func (c *Client) UpdateSchema(ctx context.Context, name string, update SchemaUpdate) (Schema, error) {
	var rows []Schema

	if err := c.Patch(ctx, ConsoleProfile, "schemas", []Filter{Eq("name", name)}, update, &rows); err != nil {
		return Schema{}, err
	}

	return onlyRow(rows, "schemas")
}

// DeleteSchema removes the schema. The sync then purges its data, which can take minutes.
func (c *Client) DeleteSchema(ctx context.Context, name string) error {
	return c.Delete(ctx, ConsoleProfile, "schemas", []Filter{Eq("name", name)}, nil)
}

// CreateTable adds the table and returns its row.
func (c *Client) CreateTable(ctx context.Context, input TableInput) (Table, error) {
	var rows []Table

	if err := c.Insert(ctx, ConsoleProfile, "tables", input, &rows); err != nil {
		return Table{}, err
	}

	return onlyRow(rows, "tables")
}

// GetTable reads the table. It wraps ErrNoRow when the table doesn't exist.
func (c *Client) GetTable(ctx context.Context, name string) (Table, error) {
	var table Table

	err := c.GetOne(ctx, ConsoleProfile, "tables", []Filter{Eq("name", name)}, &table)

	return table, err
}

// UpdateTable changes the columns that a table allows to change and returns its row.
func (c *Client) UpdateTable(ctx context.Context, name string, update TableUpdate) (Table, error) {
	var rows []Table

	if err := c.Patch(ctx, ConsoleProfile, "tables", []Filter{Eq("name", name)}, update, &rows); err != nil {
		return Table{}, err
	}

	return onlyRow(rows, "tables")
}

// DeleteTable removes the table and its row policies. The sync then drops its data.
func (c *Client) DeleteTable(ctx context.Context, name string) error {
	return c.Delete(ctx, ConsoleProfile, "tables", []Filter{Eq("name", name)}, nil)
}

// ListRLS returns the row policies of the table in order, read through console.rules(). The token role has no right
// on the table of rules, because a flat read would show the conditions of a group as alternatives.
func (c *Client) ListRLS(ctx context.Context, table string) ([]RLSRule, error) {
	var rows []rulesRow

	if err := c.RPC(ctx, ConsoleProfile, "rules", nil, &rows); err != nil {
		return nil, err
	}

	for _, row := range rows {
		if row.TableName == table {
			return row.Groups, nil
		}
	}

	return []RLSRule{}, nil
}

// ReplaceRLS replaces the row policies of the table with the given ones, in order, in one call. The database numbers
// the groups, so a caller passes relations and never a number.
func (c *Client) ReplaceRLS(ctx context.Context, table string, rules []RLSRule) error {
	if rules == nil {
		rules = []RLSRule{}
	}

	args := map[string]any{"table_name": table, "groups": rules}

	if err := c.RPC(ctx, ConsoleProfile, "replace_rls", args, nil); err != nil {
		return fmt.Errorf("replace the row policies of %s: %w", table, err)
	}

	return nil
}

// GetHealth reads the view console.health for the schema. It wraps ErrNoRow when the schema doesn't exist.
func (c *Client) GetHealth(ctx context.Context, schema string) (Health, error) {
	var health Health

	err := c.GetOne(ctx, ConsoleProfile, "health", []Filter{Eq("name", schema)}, &health)

	return health, err
}

// GetSyncState reads the view console.synced for the table. It wraps ErrNoRow when the table doesn't exist.
func (c *Client) GetSyncState(ctx context.Context, table string) (TableSyncState, error) {
	var state TableSyncState

	err := c.GetOne(ctx, ConsoleProfile, "synced", []Filter{Eq("name", table)}, &state)

	return state, err
}

// ListHistory returns the newest changes of the configuration first, at most limit of them. An empty schema means
// every schema that the token can see.
func (c *Client) ListHistory(ctx context.Context, schema string, limit int) ([]HistoryEntry, error) {
	filters := []Filter{OrderBy("changed_at", true), Limit(limit)}
	if schema != "" {
		filters = append(filters, Eq("schema_name", schema))
	}

	var entries []HistoryEntry

	if err := c.Get(ctx, ConsoleProfile, "config_history", filters, &entries); err != nil {
		return nil, err
	}

	return entries, nil
}

// ExportConfig returns the configuration grouped by project, as the JSON document that the console exports.
func (c *Client) ExportConfig(ctx context.Context) (json.RawMessage, error) {
	var document json.RawMessage

	if err := c.GetRPC(ctx, ConsoleProfile, "export_config", &document); err != nil {
		return nil, fmt.Errorf("export the configuration: %w", err)
	}

	return document, nil
}

// SyncConfig returns the flat configuration that the sync engine reads, with the physical schema names as keys.
func (c *Client) SyncConfig(ctx context.Context) (json.RawMessage, error) {
	var document json.RawMessage

	if err := c.GetRPC(ctx, ConsoleProfile, "sync_config", &document); err != nil {
		return nil, fmt.Errorf("read the sync configuration: %w", err)
	}

	return document, nil
}
