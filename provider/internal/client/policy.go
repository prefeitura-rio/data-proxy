package client

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"
)

// accessProfile is the profile of the schema access, which holds the grants.
const accessProfile = "access"

// Grant is a row of access.policy. The scope is `*` for every project, or `project`, `project.schema` or
// `project.schema.table`.
type Grant struct {
	Scope    string `json:"scope"`
	Subject  string `json:"subject"`
	UnitType string `json:"unit_type"`
	UnitID   string `json:"unit_id"`
}

// provisionInterval is the time between two checks while the client waits for the sync.
const provisionInterval = 2 * time.Second

// everyScope is the scope that stands for every project. The database stores it as the empty path.
const everyScope = "*"

// scopePath turns the scope of a grant into the path that the database stores: the scope * is the empty path.
func scopePath(scope string) string {
	if scope == everyScope {
		return ""
	}

	return scope
}

// scopeOf turns a stored path into the scope of a grant: the empty path is the scope *.
func scopeOf(path string) string {
	if path == "" {
		return everyScope
	}

	return path
}

// grantBody builds the JSON body that adds a grant.
func grantBody(grant Grant) map[string]string {
	return map[string]string{
		"scope":     scopePath(grant.Scope),
		"subject":   grant.Subject,
		"unit_type": grant.UnitType,
		"unit_id":   grant.UnitID,
	}
}

// grantFilters builds the filters that select one grant by its four columns.
func grantFilters(grant Grant) []Filter {
	return []Filter{
		Eq("scope", scopePath(grant.Scope)),
		Eq("subject", grant.Subject),
		Eq("unit_type", grant.UnitType),
		Eq("unit_id", grant.UnitID),
	}
}

// CreateGrant adds the row to access.policy.
func (c *Client) CreateGrant(ctx context.Context, grant Grant) error {
	return c.Insert(ctx, accessProfile, "policy", grantBody(grant), nil)
}

// ListGrants returns the rows that match the grant. The unique key makes at most one.
func (c *Client) ListGrants(ctx context.Context, grant Grant) ([]Grant, error) {
	var rows []Grant

	err := c.Get(ctx, accessProfile, "policy", grantFilters(grant), &rows)
	if err != nil {
		return nil, err
	}

	for i := range rows {
		rows[i].Scope = scopeOf(rows[i].Scope)
	}

	return rows, nil
}

// DeleteGrant removes the row that matches the grant.
func (c *Client) DeleteGrant(ctx context.Context, grant Grant) error {
	return c.Delete(ctx, accessProfile, "policy", grantFilters(grant), nil)
}

// IsNotProvisioned reports whether the error says that PostgREST doesn't serve the profile: it doesn't know the
// profile, or the profile has no such table.
func IsNotProvisioned(err error) bool {
	var apiErr *Error

	if !errors.As(err, &apiErr) {
		return false
	}

	switch apiErr.Code {
	case "PGRST106", "PGRST205", "42P01", "3F000":
		return true
	}

	return apiErr.Status == http.StatusNotFound || apiErr.Status == http.StatusNotAcceptable
}

// served reports whether PostgREST serves the schema. A schema that is not provisioned gives false and no error.
func (c *Client) served(ctx context.Context, schema string) (bool, error) {
	err := c.do(ctx, request{method: http.MethodGet, profile: schema, rpc: true}, nil)

	switch {
	case err == nil:
		return true, nil
	case IsNotProvisioned(err):
		return false, nil
	default:
		return false, err
	}
}

// WaitProvisioned waits until the sync has created the schema: the view console.health shows it, and PostgREST
// serves it. The context limits the wait.
func (c *Client) WaitProvisioned(ctx context.Context, schema string) error {
	return c.waitProvisioned(ctx, schema, waitFor)
}

// waitProvisioned polls until the schema is ready. The wait function sleeps between two checks, so a test can replace
// it.
func (c *Client) waitProvisioned(
	ctx context.Context,
	schema string,
	wait func(context.Context, time.Duration) error,
) error {
	for {
		ready, err := c.isReady(ctx, schema)
		if err != nil {
			return err
		}

		if ready {
			return nil
		}

		err = wait(ctx, provisionInterval)
		if err != nil {
			return fmt.Errorf("wait for the schema %s to be provisioned: %w", schema, err)
		}
	}
}

// isReady reports whether the sync has created the schema and PostgREST serves it.
func (c *Client) isReady(ctx context.Context, schema string) (bool, error) {
	health, err := c.GetHealth(ctx, schema)

	switch {
	case errors.Is(err, ErrNoRow):
		return false, nil
	case err != nil:
		return false, err
	case !health.SchemaExists:
		return false, nil
	}

	return c.served(ctx, schema)
}

// WaitDeprovisioned waits until the sync has dropped the schema, so that PostgREST no longer serves it.
// The context limits the wait.
func (c *Client) WaitDeprovisioned(ctx context.Context, schema string) error {
	return c.waitDeprovisioned(ctx, schema, waitFor)
}

// waitDeprovisioned polls until PostgREST no longer serves the schema. The wait function sleeps between two checks.
func (c *Client) waitDeprovisioned(
	ctx context.Context,
	schema string,
	wait func(context.Context, time.Duration) error,
) error {
	for {
		ready, err := c.served(ctx, schema)
		if err != nil {
			return err
		}

		if !ready {
			return nil
		}

		err = wait(ctx, provisionInterval)
		if err != nil {
			return fmt.Errorf("wait for the schema %s to be dropped: %w", schema, err)
		}
	}
}
