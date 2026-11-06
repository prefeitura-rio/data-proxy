package client

import (
	"context"
	"errors"
	"fmt"
	"time"
)

// AccessPolicy is a row of the access_policy table of a data schema. A nil UnitType or UnitID is a NULL.
type AccessPolicy struct {
	Subject  string  `json:"subject"`
	UnitType *string `json:"unit_type"`
	UnitID   *string `json:"unit_id"`
}

// provisionInterval is how long the wait for a schema pauses between two checks.
const provisionInterval = 2 * time.Second

// policyFilters selects the rows of a policy. A missing unit matches a NULL, because eq.null matches nothing.
func policyFilters(policy AccessPolicy) []Filter {
	filters := []Filter{Eq("subject", policy.Subject)}

	for _, column := range []struct {
		name  string
		value *string
	}{{"unit_type", policy.UnitType}, {"unit_id", policy.UnitID}} {
		if column.value == nil {
			filters = append(filters, IsNull(column.name))
		} else {
			filters = append(filters, Eq(column.name, *column.value))
		}
	}

	return filters
}

// CreateAccessPolicy adds the row to the access_policy table of the schema. The schema must be provisioned.
func (c *Client) CreateAccessPolicy(ctx context.Context, schema string, policy AccessPolicy) error {
	return c.Insert(ctx, schema, "access_policy", policy, nil)
}

// CreateAccessPolicyWhenProvisioned adds the row, and tries again every few seconds while the schema has no
// access_policy table yet. The context limits the wait.
func (c *Client) CreateAccessPolicyWhenProvisioned(ctx context.Context, schema string, policy AccessPolicy) error {
	return c.createAccessPolicyWhenProvisioned(ctx, schema, policy, sleep)
}

// createAccessPolicyWhenProvisioned is CreateAccessPolicyWhenProvisioned with a wait that a test replaces.
func (c *Client) createAccessPolicyWhenProvisioned(ctx context.Context, schema string, policy AccessPolicy, wait func(context.Context, time.Duration) error) error {
	for {
		err := c.CreateAccessPolicy(ctx, schema, policy)
		if !IsNotProvisioned(err) {
			return err
		}

		if waitErr := wait(ctx, provisionInterval); waitErr != nil {
			return fmt.Errorf("wait for the schema %s to be provisioned: %w", schema, errors.Join(waitErr, err))
		}
	}
}

// ListAccessPolicy returns the rows that match the policy. The table has no primary key, so more than one row is
// possible.
func (c *Client) ListAccessPolicy(ctx context.Context, schema string, policy AccessPolicy) ([]AccessPolicy, error) {
	var rows []AccessPolicy

	if err := c.Get(ctx, schema, "access_policy", policyFilters(policy), &rows); err != nil {
		return nil, err
	}

	return rows, nil
}

// DeleteAccessPolicy removes the rows that match the policy.
func (c *Client) DeleteAccessPolicy(ctx context.Context, schema string, policy AccessPolicy) error {
	return c.Delete(ctx, schema, "access_policy", policyFilters(policy), nil)
}

// IsNotProvisioned reports whether the error says that the schema has no access_policy table yet: PostgREST doesn't
// know the profile or the table, or PostgreSQL has no such relation.
func IsNotProvisioned(err error) bool {
	var apiErr *Error

	if !errors.As(err, &apiErr) {
		return false
	}

	switch apiErr.Code {
	case "PGRST106", "PGRST205", "42P01", "3F000":
		return true
	}

	return apiErr.Status == 404 || apiErr.Status == 406
}

// provisioned reports whether the schema answers a read of its access_policy table.
func (c *Client) provisioned(ctx context.Context, schema string) (bool, error) {
	err := c.Get(ctx, schema, "access_policy", []Filter{Limit(0)}, nil)

	switch {
	case err == nil:
		return true, nil
	case IsNotProvisioned(err):
		return false, nil
	default:
		return false, err
	}
}

// WaitProvisioned waits until the sync has created the schema: the view console.health shows the schema and its
// access_policy table, and PostgREST answers a read of the table. The context limits the wait.
func (c *Client) WaitProvisioned(ctx context.Context, schema string) error {
	return c.waitProvisioned(ctx, schema, sleep)
}

// waitProvisioned is WaitProvisioned with a wait that a test replaces.
func (c *Client) waitProvisioned(ctx context.Context, schema string, wait func(context.Context, time.Duration) error) error {
	for {
		ready, err := c.isReady(ctx, schema)
		if err != nil {
			return err
		}

		if ready {
			return nil
		}

		if err := wait(ctx, provisionInterval); err != nil {
			return fmt.Errorf("wait for the schema %s to be provisioned: %w", schema, err)
		}
	}
}

// isReady reports whether the schema shows in the health view and answers a read of its access_policy table.
func (c *Client) isReady(ctx context.Context, schema string) (bool, error) {
	health, err := c.GetHealth(ctx, schema)

	switch {
	case errors.Is(err, ErrNoRow):
		return false, nil
	case err != nil:
		return false, err
	case !health.SchemaExists || !health.AccessPolicy:
		return false, nil
	}

	return c.provisioned(ctx, schema)
}

// WaitDeprovisioned waits until the sync has dropped the schema, so that a read of its access_policy table fails.
// The context limits the wait.
func (c *Client) WaitDeprovisioned(ctx context.Context, schema string) error {
	return c.waitDeprovisioned(ctx, schema, sleep)
}

// waitDeprovisioned is WaitDeprovisioned with a wait that a test replaces.
func (c *Client) waitDeprovisioned(ctx context.Context, schema string, wait func(context.Context, time.Duration) error) error {
	for {
		ready, err := c.provisioned(ctx, schema)
		if err != nil {
			return err
		}

		if !ready {
			return nil
		}

		if err := wait(ctx, provisionInterval); err != nil {
			return fmt.Errorf("wait for the schema %s to be dropped: %w", schema, err)
		}
	}
}
