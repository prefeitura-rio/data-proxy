package client

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func TestAccessPolicyOperations(t *testing.T) {
	tests := []struct {
		name   string
		policy AccessPolicy
		uri    string
	}{
		{
			name:   "a policy with a unit",
			policy: AccessPolicy{Subject: "reader@example.org", UnitType: ptr("unit"), UnitID: ptr("42")},
			uri:    "/access_policy?subject=eq.reader%40example.org&unit_type=eq.unit&unit_id=eq.42",
		},
		{
			name:   "a policy without a unit matches the NULLs",
			policy: AccessPolicy{Subject: "svc"},
			uri:    "/access_policy?subject=eq.svc&unit_type=is.null&unit_id=is.null",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			row := `{"subject":"` + tt.policy.Subject + `","unit_type":null,"unit_id":null}`
			c := newStepClient(t, []step{
				{method: "POST", uri: "/access_policy", contentProfile: "sales_orders", status: 201, response: "[]"},
				{method: "GET", uri: tt.uri, acceptProfile: "sales_orders", response: "[" + row + "]"},
				{method: "DELETE", uri: tt.uri, contentProfile: "sales_orders", response: "[]"},
			})
			ctx := context.Background()

			if err := c.CreateAccessPolicy(ctx, "sales_orders", tt.policy); err != nil {
				t.Fatalf("CreateAccessPolicy: %v", err)
			}

			rows, err := c.ListAccessPolicy(ctx, "sales_orders", tt.policy)
			if err != nil || len(rows) != 1 || rows[0].Subject != tt.policy.Subject {
				t.Fatalf("ListAccessPolicy = %+v, %v, want the one row", rows, err)
			}

			if err := c.DeleteAccessPolicy(ctx, "sales_orders", tt.policy); err != nil {
				t.Fatalf("DeleteAccessPolicy: %v", err)
			}
		})
	}
}

func TestCreateAccessPolicySendsNullUnits(t *testing.T) {
	c := newStepClient(t, []step{{
		method: "POST", uri: "/access_policy", status: 201, response: "[]",
		body: `{"subject":"svc","unit_type":null,"unit_id":null}`,
	}})

	if err := c.CreateAccessPolicy(context.Background(), "sales_orders", AccessPolicy{Subject: "svc"}); err != nil {
		t.Fatalf("CreateAccessPolicy: %v", err)
	}
}

func TestIsNotProvisioned(t *testing.T) {
	tests := []struct {
		name string
		err  error
		want bool
	}{
		{name: "an unknown profile", err: &Error{Status: 406, Code: "PGRST106"}, want: true},
		{name: "a table that the cache does not know", err: &Error{Status: 404, Code: "PGRST205"}, want: true},
		{name: "a missing relation", err: &Error{Status: 400, Code: "42P01"}, want: true},
		{name: "a missing schema", err: &Error{Status: 400, Code: "3F000"}, want: true},
		{name: "a 404 without a code", err: &Error{Status: 404}, want: true},
		{name: "a refused permission", err: &Error{Status: 403, Code: "42501"}, want: false},
		{name: "a failure that is not an API error", err: errors.New("connection refused"), want: false},
		{name: "no error", err: nil, want: false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := IsNotProvisioned(tt.err); got != tt.want {
				t.Errorf("IsNotProvisioned(%v) = %t, want %t", tt.err, got, tt.want)
			}
		})
	}
}

// noWait is a wait that returns at once and counts its calls.
type noWait struct {
	calls int
	err   error
}

// wait counts the call and returns the error that the test set.
func (n *noWait) wait(context.Context, time.Duration) error {
	n.calls++

	return n.err
}

// The answers of the health view and the error of a schema that PostgREST does not know yet.
const (
	healthMissing = `[]`
	healthPartial = `[{"name":"s","schema_exists":true,"access_policy":false,"view_count":0}]`
	healthReady   = `[{"name":"s","schema_exists":true,"access_policy":true,"view_count":0}]`
	notProvided   = `{"code":"PGRST106","message":"The schema must be one of the following"}`
)

func TestWaitProvisioned(t *testing.T) {
	tests := []struct {
		name      string
		steps     []step
		wait      noWait
		wantWaits int
		wantErr   string
	}{
		{
			name: "ready at once",
			steps: []step{
				{uri: "/health?name=eq.s", response: healthReady},
				{uri: "/access_policy?limit=0", acceptProfile: "s", response: "[]"},
			},
		},
		{
			name: "waits for the row, then the table, then the profile",
			steps: []step{
				{uri: "/health?name=eq.s", response: healthMissing},
				{uri: "/health?name=eq.s", response: healthPartial},
				{uri: "/health?name=eq.s", response: healthReady},
				{uri: "/access_policy?limit=0", status: 406, response: notProvided},
				{uri: "/health?name=eq.s", response: healthReady},
				{uri: "/access_policy?limit=0", response: "[]"},
			},
			wantWaits: 3,
		},
		{
			name:    "stops when the context ends",
			steps:   []step{{uri: "/health?name=eq.s", response: healthMissing}},
			wait:    noWait{err: context.DeadlineExceeded},
			wantErr: "wait for the schema s to be provisioned: context deadline exceeded",
		},
		{
			name:    "stops on an error that is not about provisioning",
			steps:   []step{{uri: "/health?name=eq.s", status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantErr: "HTTP 403",
		},
		{
			name: "stops on a refused read of the table",
			steps: []step{
				{uri: "/health?name=eq.s", response: healthReady},
				{uri: "/access_policy?limit=0", status: 403, response: `{"code":"42501","message":"denied"}`},
			},
			wantErr: "HTTP 403",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := newStepClient(t, tt.steps)
			wait := tt.wait

			err := c.waitProvisioned(context.Background(), "s", wait.wait)

			checkWait(t, err, tt.wantErr)

			if tt.wantErr == "" && wait.calls != tt.wantWaits {
				t.Errorf("waits = %d, want %d", wait.calls, tt.wantWaits)
			}
		})
	}
}

func TestWaitDeprovisioned(t *testing.T) {
	tests := []struct {
		name      string
		steps     []step
		wait      noWait
		wantWaits int
		wantErr   string
	}{
		{name: "already dropped", steps: []step{{status: 406, response: notProvided}}},
		{
			name: "waits until the read fails",
			steps: []step{
				{response: "[]"},
				{response: "[]"},
				{status: 404, response: `{"code":"PGRST205","message":"not found"}`},
			},
			wantWaits: 2,
		},
		{
			name:    "stops when the context ends",
			steps:   []step{{response: "[]"}},
			wait:    noWait{err: context.Canceled},
			wantErr: "wait for the schema s to be dropped: context canceled",
		},
		{
			name:    "stops on an error that is not about provisioning",
			steps:   []step{{status: 403, response: `{"code":"42501","message":"denied"}`}},
			wantErr: "HTTP 403",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := newStepClient(t, tt.steps)
			wait := tt.wait

			err := c.waitDeprovisioned(context.Background(), "s", wait.wait)

			checkWait(t, err, tt.wantErr)

			if tt.wantErr == "" && wait.calls != tt.wantWaits {
				t.Errorf("waits = %d, want %d", wait.calls, tt.wantWaits)
			}
		})
	}
}

// checkWait compares the error of a wait with the text that the test wants, and no text means no error.
func checkWait(t *testing.T, err error, want string) {
	t.Helper()

	switch {
	case want == "" && err != nil:
		t.Fatalf("error = %v, want none", err)
	case want != "" && (err == nil || !strings.Contains(err.Error(), want)):
		t.Fatalf("error = %v, want one with %q", err, want)
	}
}

func TestCreateAccessPolicyWhenProvisioned(t *testing.T) {
	tests := []struct {
		name      string
		steps     []step
		wait      noWait
		wantWaits int
		wantErr   string
	}{
		{name: "adds the row at once", steps: []step{{status: 201}}},
		{
			name:      "tries again until the schema is provisioned",
			steps:     []step{{status: 406, response: notProvided}, {status: 404, response: `{"code":"PGRST205","message":"not found"}`}, {status: 201}},
			wantWaits: 2,
		},
		{
			name:    "stops when the context ends",
			steps:   []step{{status: 406, response: notProvided}},
			wait:    noWait{err: context.DeadlineExceeded},
			wantErr: "wait for the schema s to be provisioned",
		},
		{name: "stops on an error that is not about provisioning", steps: []step{{status: 403, response: `{"code":"42501","message":"denied"}`}}, wantErr: "HTTP 403"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := newStepClient(t, tt.steps)
			wait := tt.wait

			err := c.createAccessPolicyWhenProvisioned(context.Background(), "s", AccessPolicy{Subject: "svc"}, wait.wait)

			checkWait(t, err, tt.wantErr)

			if tt.wantErr == "" && wait.calls != tt.wantWaits {
				t.Errorf("waits = %d, want %d", wait.calls, tt.wantWaits)
			}
		})
	}
}
